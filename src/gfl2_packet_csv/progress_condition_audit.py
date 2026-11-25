"""Aggregate evidence joining group 10 to static helix level conditions.

This is a diagnostic, not a completion decoder. Missing records are not zero;
no player IDs, masks, or private values are returned.
"""
from collections import Counter, defaultdict
from .helix_catalog import catalog
from .progression_reference_catalog import node_progression


def audit_progress_conditions(bundle):
    rows = bundle.rows
    if not {'account_progress_entries.csv', 'dolls.csv'} <= rows.keys():
        return {'status': 'snapshot_unavailable', 'counts': {}, 'distribution': []}
    # A core CSV always exists, even when its packet was not observed.
    packets = bundle.manifest['account_packet_coverage']['packets']
    if not any(p['packet_id'] == 11101 and p['core_decoder'] == 'observed'
               and p['decode_errors'] == 0 for p in packets):
        return {'status': 'snapshot_unavailable', 'counts': {}, 'distribution': []}
    groups = catalog()
    conditions = {}
    for doll, group in groups.items():
        for node in group.nodes:
            progression = node_progression(node.node_record_id)
            if progression is None:
                return {'status': 'catalog_unavailable', 'counts': {}, 'distribution': []}
            c = progression.condition
            if c is not None:
                key = (doll, c.required_level)
                if conditions.setdefault(c.condition_id, key) != key:
                    return {'status': 'catalog_inconsistent', 'counts': {}, 'distribution': []}
    if not conditions:
        return {'status': 'catalog_unavailable', 'counts': {}, 'distribution': []}
    levels = {int(r['doll_id']): int(r['level']) if r['level'] else None
              for r in rows['dolls.csv']}
    # Additional form is not automatically an ordinary doll level.
    observed = defaultdict(list)
    for r in rows['account_progress_entries.csv']:
        if r['group_id_raw'] == '10' and r['source_field'] == '4' and r['value1_raw']:
            observed[int(r['value1_raw'])].append(r)
    dist = Counter()
    for cid, (doll, required) in conditions.items():
        level = levels.get(doll)
        eligibility = ('level_unavailable' if level is None else
                       'level_met' if level >= required else 'level_below')
        records = observed.get(cid, [])
        if not records:
            state = 'absent'
        elif len(records) != 1:
            state = 'multiple_records'
        elif records[0]['decoded_pair'] != '1' or records[0]['value2_observation_status'] != 'binary_code_observed':
            state = 'unresolved'
        else:
            state = 'code_' + records[0]['value2_raw']
        dist[eligibility, state] += 1
    return {'status': 'candidate_level_condition_consistency_not_completion_proof',
            'counts': {'static_conditions': len(conditions),
                       'observed_condition_ids': sum(cid in observed for cid in conditions)},
            'distribution': [dict(level_relation=level, observation=state, count=count)
                             for (level, state), count in sorted(dist.items())]}
