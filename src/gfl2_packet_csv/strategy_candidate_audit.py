"""Aggregate checks of candidate strategy parameter mappings, not behavior proof.

Input must be a validated AccountBundle. No model IDs or raw values returned.
Absent tables and observed empty tables are intentionally distinct.
"""
from collections import Counter
from .ai_target_catalog import prefix_reference_status
from .skill_parameters import projection

SOURCES = {
    'current': ('doll_skills.csv', 'parameter_raw'),
    'inactive': ('doll_saved_strategy_skills.csv', 'parameter_raw'),
    'formation': ('formation_slot_skills.csv', 'parameter_code'),
    'additional': ('additional_doll_skills.csv', 'parameter_code'),
}


def audit_strategy_candidates(bundle):
    sources = {}
    total_prefix, total_parameter = Counter(), Counter()
    for source, (name, field) in SOURCES.items():
        if name not in bundle.rows:
            sources[source] = {'status': 'not_emitted', 'rows': None,
                               'prefix_status_counts': None, 'parameter_status_counts': None}
            continue
        rows = bundle.rows[name]
        prefix, parameter = Counter(), Counter()
        for row in rows:
            raw = None if row[field] == '' else int(row[field])
            prefix[prefix_reference_status(int(row['skill_id']), raw)] += 1
            parameter[projection(raw)[-1]] += 1
        total_prefix.update(prefix)
        total_parameter.update(parameter)
        sources[source] = {'status': 'observed', 'rows': len(rows),
                           'prefix_status_counts': dict(sorted(prefix.items())),
                           'parameter_status_counts': dict(sorted(parameter.items()))}
    return {'schema_version': 1, 'semantic_status': 'candidate_consistency_not_behavior_proof',
            'sources': sources, 'rows': sum(total_prefix.values()),
            'prefix_status_counts': dict(sorted(total_prefix.items())),
            'parameter_status_counts': dict(sorted(total_parameter.items()))}
