"""Count-only offline audit of current equipment, from a validated V35 bundle.

Missing observations are not proof of deletion. Saved formations and separate
promotion records are not current ownership claims. No identifiers are returned.
"""
from collections import Counter, defaultdict


def _positive(value):
    return int(value) if value not in ('', None, '0', 0) else None


def _relation(claims, inventory, reverse, *, source_ready, target_ready):
    """claims: (owner, item) positions; reverse: item -> observed owner or None."""
    result = Counter()
    grouped = defaultdict(list)
    for owner, item in claims:
        grouped[item].append(owner)
    result['positive_claims'] = len(claims)
    result['distinct_claimed_items'] = len(grouped)
    result['repeated_same_owner_positions'] = sum(len(v)-len(set(v)) for v in grouped.values())
    owner_sets = {item: set(v) for item, v in grouped.items()}
    result['multiple_owner_items'] = sum(len(v) > 1 for v in owner_sets.values())
    for owner, item in claims:
        if not source_ready or not target_ready:
            state = 'snapshot_unavailable'
        elif item not in inventory:
            state = 'item_not_in_observed_inventory'
        elif reverse.get(item) is None:
            state = 'reverse_owner_not_observed'
        elif reverse[item] != owner:
            state = 'reverse_owner_differs'
        elif len(owner_sets[item]) > 1:
            state = 'multiple_owners'
        else:
            state = 'matching_owner'
        result[state] += 1
    for item, owner in reverse.items():
        if owner is None:
            continue
        if not source_ready or not target_ready:
            result['reverse_snapshot_unavailable'] += 1
        elif owner not in owner_sets.get(item, ()):
            result['reverse_without_matching_claim'] += 1
        else:
            result['reverse_with_matching_claim'] += 1
    return {'source_ready': source_ready, 'target_ready': target_ready,
            'counts': dict(sorted(result.items()))}


def audit_equipment_graph(bundle):
    """Accept an AccountBundle returned by read_bundle, never mutate it."""
    rows = bundle.rows
    def ready(packet):
        return any(p['packet_id'] == packet and p['capture_state'] == 'partial_decoder_observed'
                   for p in bundle.manifest['account_packet_coverage']['packets'])
    def inventory(name, field='uid'):
        return {int(r[field]): r for r in rows.get(name, [])}
    def claims(name, owner, item, filter_row=lambda r: True):
        return [(int(r[owner]), int(r[item])) for r in rows.get(name, [])
                if filter_row(r) and _positive(r[item]) is not None]
    dolls = inventory('dolls.csv', 'doll_id')
    weapons = inventory('weapons.csv')
    parts = inventory('attachments.csv')
    keys = inventory('common_keys.csv')
    remold = inventory('remolding_details.csv')
    owners = defaultdict(dict)
    for r in rows.get('equipment_owner_links.csv', []):
        owners[r['kind']][int(r['uid'])] = _positive(r['owner_doll_id'])
    doll_ready = ready(11101)
    doll_ext_ready = doll_ready and bundle.extension['status']['dolls'] == 'observed'
    slot_ready = ready(11021) and 'weapon_slot_observations.csv' in rows and (
        bundle.manifest['weapon_slot_observations']['status'] == 'validated_observed_links')
    weapon_reverse = {uid: _positive(r['equipped_doll_id']) for uid, r in weapons.items()}
    part_reverse = {uid: _positive(r['weapon_uid']) for uid, r in parts.items()}
    out = {
        'schema_version': 1,
        'current_doll_weapons': _relation(claims('dolls.csv', 'doll_id', 'weapon_uid'), weapons,
            weapon_reverse, source_ready=doll_ready, target_ready=ready(11021)),
        'current_weapon_parts': _relation(claims('weapon_slot_observations.csv', 'weapon_uid', 'attachment_uid'),
            parts, part_reverse, source_ready=slot_ready, target_ready=ready(11061)),
        'current_doll_keys': _relation(claims('doll_key_values.csv', 'doll_id', 'value',
            lambda r: r['kind'] in ('common_uids', 'bond_uids')), keys, owners['common_key'],
            source_ready=doll_ext_ready, target_ready=ready(11138)),
        'current_doll_remoldings': _relation(claims('doll_core_slots.csv', 'doll_id', 'uid'), remold,
            owners['remolding'], source_ready=doll_ext_ready, target_ready=ready(11163)),
    }
    # The independent record may describe an alternate form; do not merge it
    # into the current graph or interpret overlaps as double equipment.
    extra_claims = claims('additional_doll_observations.csv', 'doll_id', 'weapon_uid')
    extra_keys = claims('additional_doll_key_slots.csv', 'doll_id', 'key_uid')
    extra_ids = {int(r['doll_id']) for r in rows.get('additional_doll_observations.csv', [])}
    current_keys = {item for _, item in claims('doll_key_values.csv', 'doll_id', 'value',
                     lambda r: r['kind'] in ('common_uids', 'bond_uids'))}
    out['separate_records'] = {
        'doll_records': len(extra_ids),
        'weapon_claims': len(extra_claims),
        'key_claims': len(extra_keys),
        'weapon_claims_with_matching_reverse': sum(weapon_reverse.get(uid) == did for did, uid in extra_claims) if ready(11021) else None,
        'key_claims_overlapping_current_slots': sum(uid in current_keys for _, uid in extra_keys) if doll_ext_ready else None,
        'weapon_owners_outside_normal_dolls': sum(owner is not None and owner not in dolls for owner in weapon_reverse.values()) if doll_ready and ready(11021) else None,
        'weapon_owners_matching_separate_record_id': sum(owner in extra_ids for owner in weapon_reverse.values()) if doll_ready and ready(11021) else None,
    }
    return out


def main():
    import argparse
    import json
    from .bundle_import import read_bundle
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle')
    parser.add_argument('--allow-partial', action='store_true')
    args = parser.parse_args()
    try:
        report = audit_equipment_graph(read_bundle(args.bundle, allow_partial=args.allow_partial))
    except (ValueError, OSError):
        parser.exit(1, 'Account bundle could not be validated.\n')
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
