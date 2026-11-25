"""Evidence-only audit for four partially mapped account packet families.

All returned fields are finite counts/status labels. Values and identifiers
are used in memory only and must never enter ordinary/debug archives.
Matching account links is evidence, NOT an authoritative protobuf schema.
"""
from __future__ import annotations


def audit_core_unknown_fields(unmapped: dict, *, dolls: dict, common_keys: dict,
                              remoldings: dict, equipment_dolls: dict,
                              doll_extension: dict | None,
                              received: set[int]) -> dict:
    counts = {f'{packet}.{field}': 0 for packet, fields in (
        (11021, (7, 8)), (11101, (2, 4)), (11138, (3, 4)),
        (11163, (4,))) for field in fields}
    for (packet, _uid, field), values in unmapped.items():
        k = f'{packet}.{field}'
        if k in counts:
            counts[k] += len(values)

    ownership: dict[int, set[int]] = {}
    for doll_id, eq in equipment_dolls.items():
        for uid in eq.get('common_key_uids', ()):
            ownership.setdefault(int(uid), set()).add(int(doll_id))
    core_slots: dict[int, set[int]] = {}
    bond_slot_count = 0
    if doll_extension:
        for doll in doll_extension.get('rows', {}).values():
            # 11101.45 is the ordinary common-key UID list, whereas
            # 11101.60 holds a second key family. Both families originate
            # from 11138.3. Ignoring field 60 falsely labels 52 equipped
            # references as unexplained in the four controlled captures.
            for uid in doll.get('bond_key_uids_raw') or ():
                if uid is not None:
                    ownership.setdefault(int(uid), set()).add(int(doll['doll_id']))
                    bond_slot_count += 1
            core = doll.get('core') or {}
            for uid in core.get('remolding_slots') or ():
                if uid is not None:
                    core_slots.setdefault(int(uid), set()).add(int(doll['doll_id']))
    known_dolls = set(map(int, dolls))
    refs = {
        '11138.3': {'observed': 0, 'references_owned_doll': 0,
                    'currently_equipped_match': 0, 'no_current_equipment_evidence': 0},
        '11163.4': {'observed': 0, 'references_owned_doll': 0,
                    'currently_equipped_match': 0, 'no_current_equipment_evidence': 0},
    }
    for (packet, uid, field), values in unmapped.items():
        k = f'{packet}.{field}'
        if k not in refs:
            continue
        target = refs[k]
        owners = ownership.get(uid) if packet == 11138 else core_slots.get(uid)
        for wire, encoded in values:
            if wire != 0:
                continue
            target['observed'] += 1
            referenced_id = int(encoded)
            if referenced_id in known_dolls:
                target['references_owned_doll'] += 1
            if not owners:
                target['no_current_equipment_evidence'] += 1
            elif referenced_id in owners:
                target['currently_equipped_match'] += 1

    # RC46: controlled equip / unequip observations confirmed field 11138.3
    # and 11163.4 change with the target doll. A cross-capture check found
    # that all 52 apparent nonmatches in the previous audit were actually
    # equipped via 11101.60, not via ordinary common-key slots 11101.45.
    # Only pairs corroborated by both wire fields should be treated as active.
    def slot_consistency(packet: int, field: int, slots: dict[int, set[int]]):
        title = f'{packet}.{field}'
        known = packet in received and 11101 in received and doll_extension is not None
        summary = {
            'status': 'unavailable',
            'current_slot_claims': 0,
            'field_claims': 0,
            'matched_current_slot_claims': 0,
            'field_claims_without_current_slot': 0,
            'current_slot_claims_without_field': 0,
            'different_current_slot_doll': 0,
            'ambiguous_multi_doll_slots': 0,
        }
        if not known:
            return summary
        asserted: dict[int, list[int]] = {}
        for (packet_type, uid, raw_field), values in unmapped.items():
            if (packet_type, raw_field) != (packet, field):
                continue
            valid_values = [int(encoded) for wire, encoded in values if wire == 0]
            if valid_values:
                asserted.setdefault(int(uid), []).extend(valid_values)
        summary['field_claims'] = sum(map(len, asserted.values()))
        summary['current_slot_claims'] = sum(map(len, slots.values()))
        summary['ambiguous_multi_doll_slots'] = sum(len(v) > 1 for v in slots.values())
        for uid, referenced_ids in asserted.items():
            current = slots.get(uid)
            if not current:
                summary['field_claims_without_current_slot'] += len(referenced_ids)
            else:
                for doll_id in referenced_ids:
                    if doll_id in current:
                        summary['matched_current_slot_claims'] += 1
                    else:
                        summary['different_current_slot_doll'] += 1
        summary['current_slot_claims_without_field'] = sum(
            len(owners) for uid, owners in slots.items() if uid not in asserted
        )
        if (summary['different_current_slot_doll'] or
                summary['current_slot_claims_without_field'] or
                summary['ambiguous_multi_doll_slots']):
            summary['status'] = 'needs_review'
        elif summary['field_claims_without_current_slot']:
            summary['status'] = 'active_slots_verified_extra_associations_unconfirmed'
        else:
            summary['status'] = 'observed_active_slots_consistent'
        return summary

    slot_audit = {
        '11138.3': slot_consistency(11138, 3, ownership),
        '11163.4': slot_consistency(11163, 4, core_slots),
    }

    return {
        'schema_version': 2,
        'semantic_status': 'observed_links_with_controlled_transitions_not_full_protocol_schema',
        'current_slot_consistency': slot_audit,
        'additional_bond_key_slot_claims': bond_slot_count,
        'field_occurrences': counts,
        'reference_relationships': {
            k: {**v, 'cross_check_state': (
                'observed' if int(k.split('.')[0]) in received and
                11101 in received else 'unavailable')}
            for k, v in refs.items()
        },
        'unknown_fields_not_exported': True,
    }
