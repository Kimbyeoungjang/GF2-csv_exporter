"""Offline aggregate checks of a candidate helix bit-to-node mapping.

Input is an already validated AccountBundle. No unlock state is asserted;
no IDs, raw masks, or account-specific rows are returned. In particular,
level and edge consistency cannot distinguish permutations of set bits.
"""
from collections import Counter, defaultdict
from .helix_catalog import catalog as helix_catalog
from .progression_reference_catalog import node_progression


def audit_helix_candidates(bundle):
    rows = bundle.rows
    counts = dict.fromkeys((
        'ordinary_dolls', 'mask_absent', 'multiple_masks', 'unknown_group',
        'outside_range', 'catalog_unavailable', 'checked_masks', 'set_bits',
        'unset_bits', 'set_bits_with_level_condition', 'set_bits_without_level_condition',
        'set_bits_level_unavailable', 'set_bits_below_required_level',
        'set_bits_with_incoming_reference', 'set_bits_missing_incoming_reference',
    ), 0)
    unset = Counter()
    groups = helix_catalog()
    masks = defaultdict(list)
    for row in rows.get('doll_helix.csv', []):
        masks[int(row['doll_id'])].append(int(row['value_raw']))
    for row in rows.get('dolls.csv', []):
        counts['ordinary_dolls'] += 1
        did = int(row['doll_id'])
        values = masks[did]
        if not values:
            counts['mask_absent'] += 1
            continue
        if len(values) != 1:
            counts['multiple_masks'] += 1
            continue
        if not groups:
            counts['catalog_unavailable'] += 1
            continue
        g = groups.get(did)
        if g is None:
            counts['unknown_group'] += 1
            continue
        mask = values[0]
        if not 0 <= mask < (1 << len(g.nodes)):
            counts['outside_range'] += 1
            continue
        level = int(row['level']) if row['level'] else None
        progress = [node_progression(n.node_record_id) for n in g.nodes]
        if any(p is None for p in progress):
            counts['catalog_unavailable'] += 1
            continue
        counts['checked_masks'] += 1
        active = {n.node_record_id for n in g.nodes if mask & (1 << n.bit_index_candidate)}
        for n, p in zip(g.nodes, progress):
            condition = p.condition.required_level if p.condition else None
            if n.node_record_id not in active:
                counts['unset_bits'] += 1
                # Output only small static indices / levels and aggregate counts.
                unset[n.bit_index_candidate, condition, level is not None and condition is not None and level >= condition] += 1
                continue
            counts['set_bits'] += 1
            if condition is None:
                counts['set_bits_without_level_condition'] += 1
            else:
                counts['set_bits_with_level_condition'] += 1
                if level is None:
                    counts['set_bits_level_unavailable'] += 1
                elif level < condition:
                    counts['set_bits_below_required_level'] += 1
            if p.incoming_node_ids:
                counts['set_bits_with_incoming_reference'] += 1
                if any(i not in active for i in p.incoming_node_ids):
                    counts['set_bits_missing_incoming_reference'] += 1
    return {'semantic_status': 'candidate_mapping_consistency_not_unlock_proof',
            'counts': counts,
            'unset_bit_groups': [dict(bit_index_candidate=b, required_level=l,
                                      level_condition_met_candidate=met, count=count)
                                 for (b,l,met),count in sorted(unset.items(),key=lambda x:(x[0][0], x[0][1] or 0, x[0][2]))]}
