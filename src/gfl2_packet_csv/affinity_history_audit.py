"""Offline evidence-only affinity audit. Returns aggregate counts, never IDs or
history values. Nested fields 6/7 are candidates, not a decoded event log.
This audit does not alter account state or infer missing XP as zero.
"""
from collections import Counter
from .wire_probe import parse_fields
from .doll_growth_catalog import affinity_status


def _values(fields, number, wire):
    matches = [(w, v) for n, w, v in fields if n == number]
    if any(w != wire for w, _ in matches):
        raise ValueError('Unexpected affinity audit wire type')
    return [v for _, v in matches]


def _one(fields, number, wire=0):
    values = _values(fields, number, wire)
    if len(values) > 1:
        raise ValueError('Repeated scalar in affinity audit')
    return values[0] if values else None


def _optional_int(value):
    return None if value == '' else int(value)


def audit_affinity_history(payload: bytes, progress_rows) -> dict:
    """Compare one stored 11101 snapshot to its doll_progress.csv rows.

    No assumption about unobserved records, timestamp units or field 6 meaning.
    Explicit zero and omitted fields are distinguished throughout.
    """
    rows = {}
    for row in progress_rows:
        key = int(row['doll_id'])
        if key in rows:
            raise ValueError('Duplicate progress row')
        rows[key] = row
    seen = set()
    counts = Counter(dict.fromkeys((
        'dolls', 'csv_matches', 'csv_mismatches', 'csv_missing', 'csv_extra',
        'affinity_absent', 'current_xp_absent', 'field4_present', 'field5_present',
        'field4_field5_presence_mismatch', 'field6_records', 'field7_records',
        'field7_incomplete_pairs', 'field7_threshold_consistent',
        'field7_threshold_mismatch', 'field7_threshold_unavailable',
        'field7_dolls', 'field7_final_exact_match', 'field7_final_nonmatch', 'field7_final_unavailable',
        'field7_xp_decrease', 'field7_level_decrease', 'field7_order_value_decrease',
    ), 0))
    for raw in _values(parse_fields(payload), 1, 2):
        fields = parse_fields(raw)
        doll_id = _one(fields, 1)
        if doll_id is None or doll_id in seen:
            raise ValueError('Missing or duplicate doll in affinity audit')
        seen.add(doll_id)
        counts['dolls'] += 1
        aff_raw = _one(fields, 50, 2)
        present = aff_raw is not None
        a = parse_fields(aff_raw) if present else []
        level, xp = _one(a, 1), _one(a, 2)
        counts['affinity_absent'] += not present
        counts['current_xp_absent'] += present and xp is None
        row = rows.get(doll_id)
        if row is None:
            counts['csv_missing'] += 1
        else:
            observed = (row['affinity_present'], _optional_int(row['affinity_level']), _optional_int(row['affinity_cumulative_xp']))
            expected = ('1' if present else '0', level, xp)
            counts['csv_matches' if observed == expected else 'csv_mismatches'] += 1
        f4, f5 = _one(a, 4), _one(a, 5)
        counts['field4_present'] += f4 is not None
        counts['field5_present'] += f5 is not None
        counts['field4_field5_presence_mismatch'] += (f4 is None) != (f5 is None)
        counts['field6_records'] += len(_values(a, 6, 2))
        history = []
        for raw7 in _values(a, 7, 2):
            record = parse_fields(raw7)
            hxp, order, hlvl = (_one(record, n) for n in (1, 2, 3))
            history.append((hxp, order, hlvl))
            counts['field7_records'] += 1
            if hxp is None or hlvl is None:
                counts['field7_incomplete_pairs'] += 1
                continue
            status = affinity_status({'level': hlvl, 'cumulative_xp': hxp})
            counts['field7_' + (status if status in ('threshold_consistent', 'threshold_mismatch') else 'threshold_unavailable')] += 1
        if history:
            counts['field7_dolls'] += 1
            last = history[-1]
            # Missing values may compare equal, but do not count as full evidence.
            if None in (xp, level, last[0], last[2]):
                counts['field7_final_unavailable'] += 1
            else:
                counts['field7_final_exact_match' if (last[0], last[2]) == (xp, level) else 'field7_final_nonmatch'] += 1
            for idx, key in enumerate(('field7_xp_decrease', 'field7_order_value_decrease', 'field7_level_decrease')):
                counts[key] += sum(left[idx] is not None and right[idx] is not None and right[idx] < left[idx] for left, right in zip(history, history[1:]))
    counts['csv_extra'] = len(set(rows) - seen)
    return dict(counts)
