"""Compare two validated public 10833 projections without exposing account IDs.

This is a diagnostic of recorded observations, not a completion decoder or a
claim that the bundles belong to the same account. Withheld values cannot be
compared. Repeated group IDs are aggregated, not aligned by packet position.
"""
from collections import Counter, defaultdict

from .progress_snapshot import GROUPS_FILE, ENTRIES_FILE, validate_projection


def _index(bundle):
    if not {GROUPS_FILE, ENTRIES_FILE} <= bundle.rows.keys():
        return None
    groups, entries = bundle.rows[GROUPS_FILE], bundle.rows[ENTRIES_FILE]
    validate_projection(groups, entries)
    occurrences = Counter()
    other = Counter()
    sequences = defaultdict(list)
    for row in groups:
        group = int(row['group_id_raw']) if row['group_id_raw'] not in (None, '') else None
        occurrences[group] += 1
        other[group] += int(row['other_fields_count'])
    for row in sorted(entries, key=lambda r: (int(r['group_position']), int(r['source_field']), int(r['entry_position']))):
        group = int(row['group_id_raw']) if row['group_id_raw'] not in (None, '') else None
        field = int(row['source_field'])
        ident = int(row['value1_raw']) if row['value1_raw'] not in (None, '') else None
        code = int(row['value2_raw']) if row['value2_raw'] not in (None, '') else None
        sequences[group, field].append((ident, code, int(row['decoded_pair']), row['value2_observation_status']))
    return occurrences, other, sequences


def _by_id(sequence):
    result = defaultdict(list)
    for ident, code, pair, status in sequence:
        if ident is not None:
            result[ident].append((code, pair, status))
    return result


def _compare(a, b):
    left, right = _by_id(a), _by_id(b)
    counts = Counter({k: 0 for k in ('ids_only_before', 'ids_only_after', 'ambiguous_duplicate_ids',
        'comparable_binary_ids', 'same_binary_code', 'code_0_to_1', 'code_1_to_0',
        'unresolved_shared_ids', 'observation_changed_shared_ids')})
    counts['ids_only_before'] = len(left.keys() - right.keys())
    counts['ids_only_after'] = len(right.keys() - left.keys())
    for ident in left.keys() | right.keys():
        x, y = left.get(ident, []), right.get(ident, [])
        if len(x) > 1 or len(y) > 1:
            counts['ambiguous_duplicate_ids'] += 1
        elif x and y:
            old, new = x[0], y[0]
            if old[1:] == new[1:] == (1, 'binary_code_observed'):
                counts['comparable_binary_ids'] += 1
                counts['same_binary_code' if old[0] == new[0] else f'code_{old[0]}_to_{new[0]}'] += 1
            else:
                counts['unresolved_shared_ids'] += 1
                counts['observation_changed_shared_ids'] += int(old != new)
    ca, cb = Counter(a), Counter(b)
    return {'rows_before': len(a), 'rows_after': len(b),
            'public_rows_only_before': sum((ca-cb).values()),
            'public_rows_only_after': sum((cb-ca).values()),
            'public_multiset_equal': ca == cb,
            'public_order_only_changed': ca == cb and a != b,
            'id_absent_before': sum(r[0] is None for r in a),
            'id_absent_after': sum(r[0] is None for r in b),
            **counts}


def audit_progress_delta(before, after):
    """Return per-group/field aggregate differences of public observations.

    Both inputs should be validated AccountBundles from the same account.
    The caller is responsible for account identity; no identity is inferred.
    An absent group is not an empty group. No completion/reward inference.
    """
    first, second = _index(before), _index(after)
    if first is None or second is None:
        return {'status': 'snapshot_unavailable', 'groups': {}}
    ga, oa, ea = first
    gb, ob, eb = second
    result = {}
    for group in sorted(ga.keys() | gb.keys(), key=lambda g: (g is None, g or 0)):
        key = 'id_absent' if group is None else str(group)
        r = {'occurrences_before': ga[group], 'occurrences_after': gb[group],
             'unparsed_occurrences_before': oa[group], 'unparsed_occurrences_after': ob[group], 'fields': {}}
        result[key] = r
        if group is None:
            r['status'] = 'group_id_absent'
        elif not ga[group] or not gb[group]:
            r['status'] = 'group_not_observed_in_both'
        elif ga[group] != gb[group]:
            r['status'] = 'group_occurrence_count_changed'
        else:
            r['status'] = 'aggregated_repeated_group' if ga[group] > 1 else 'public_observations_only'
            for field in (4, 5, 6):
                a, b = ea.get((group, field), []), eb.get((group, field), [])
                if a or b:
                    r['fields'][str(field)] = _compare(a, b)
    return {'status': 'public_projection_comparison_not_completion_proof', 'groups': result}
