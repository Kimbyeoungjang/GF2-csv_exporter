"""Static BpTask category candidates for 10833; never assert task completion."""
from collections import Counter
from .reference_data import lru_cache
from types import MappingProxyType
from .doll_growth_catalog import _rows

CANDIDATE_CATEGORIES = MappingProxyType({(3, 4): 1, (6, 4): 2, (45, 4): 5})


@lru_cache(maxsize=1)
def task_categories():
    try:
        result = {}
        for r in _rows('bp_task_category_reference.csv'):
            ident, category = int(r['task_id']), int(r['field2_raw'])
            if ident <= 0 or ident in result or category not in (1, 2, 5):
                raise ValueError('invalid task category')
            result[ident] = category
        if Counter(result.values()) != {1: 337, 2: 391, 5: 186}:
            raise ValueError('incomplete static task reference')
        return MappingProxyType(result)
    except (KeyError, TypeError, ValueError):
        return MappingProxyType({})


def audit_progress_task_candidates(bundle):
    if 'account_progress_entries.csv' not in bundle.rows:
        return {'status': 'snapshot_unavailable', 'groups': {}}
    tasks = task_categories()
    if not tasks:
        return {'status': 'catalog_unavailable', 'groups': {}}
    groups = {key: Counter() for key in CANDIDATE_CATEGORIES}
    for r in bundle.rows['account_progress_entries.csv']:
        if not r['group_id_raw']:
            continue
        key = (int(r['group_id_raw']), int(r['source_field']))
        if key not in groups:
            continue
        if not r['value1_raw']:
            status = 'id_absent'
        else:
            category = tasks.get(int(r['value1_raw']))
            status = ('unrecognized' if category is None else
                      'category_consistent_candidate' if category == CANDIDATE_CATEGORIES[key]
                      else 'category_mismatch')
        groups[key][status] += 1
    return {'status': 'static_category_consistency_not_task_semantics_proof',
            'groups': {f'{g}.{f}': {'field2_candidate': CANDIDATE_CATEGORIES[g, f],
                                    'row_count': sum(counts.values()),
                                    'observations': dict(sorted(counts.items()))}
                       for (g, f), counts in groups.items()}}
