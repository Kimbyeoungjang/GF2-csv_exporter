"""Value-free evidence that ordered key slots survive normalized legacy CSVs.

The original game wire stores ordered sequences including placeholders. The
legacy equipment_dolls.csv intentionally represents normalized ID sets.
The positional account extension (doll_key_values.csv) is authoritative for
order, duplicates, and empty slots. Never publish IDs in this audit.
"""
from __future__ import annotations

KEY_FIELDS = {
    'fixed_key_ids_raw': 'fixed_key_ids',
    'common_key_ids_raw': 'common_key_ids',
    'common_key_uids_raw': 'common_key_uids',
    'expansion_key_ids_raw': 'expansion_key_ids',
}


def audit_doll_key_slots(document: dict | None, equipment: dict,
                         *, core_observed: bool, csv_exported: bool) -> dict:
    result = {
        'schema_version': 1,
        'status': 'not_observed',
        'positional_file': 'doll_key_values.csv' if csv_exported else None,
        'legacy_file': None,
        'comparison_representation': 'internal_normalized_reference_set',
        'legacy_representation': 'sorted_unique_nonzero_ids_not_positions',
        'ordered_representation': 'wire_order_with_empty_slots_and_duplicates',
        'inspected_dolls': 0,
        'slot_positions': 0,
        'empty_slot_positions': 0,
        'doll_field_sequences_reordered_or_deduplicated_in_legacy': 0,
        'legacy_projection_mismatches': 0,
    }
    if not document or document.get('status', {}).get('dolls') != 'observed':
        return result
    if not csv_exported or not core_observed:
        result['status'] = 'insufficient_snapshot'
        return result
    dolls = document['sections']['dolls']['rows']
    result['inspected_dolls'] = len(dolls)
    mismatches = 0
    for doll_id, row in dolls.items():
        existing = equipment.get(int(doll_id), {})
        for raw_field, legacy_field in KEY_FIELDS.items():
            raw_seq = row[raw_field]
            if raw_seq is None:
                continue
            original = [int(v) if v is not None else 0 for v in raw_seq]
            expected_legacy = sorted(set(v for v in original if v > 0))
            observed_legacy = [int(v) for v in existing.get(legacy_field, ())]
            result['slot_positions'] += len(original)
            result['empty_slot_positions'] += original.count(0)
            if original != expected_legacy:
                result['doll_field_sequences_reordered_or_deduplicated_in_legacy'] += 1
            if observed_legacy != expected_legacy:
                mismatches += 1
    result['legacy_projection_mismatches'] = mismatches
    result['status'] = 'verified' if not mismatches else 'legacy_projection_mismatch'
    return result
