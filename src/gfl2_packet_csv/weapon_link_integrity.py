"""Public weapon/attachment relationship gate (counts only in metadata).

A missing or partially decoded attachment snapshot must never be presented
as a set of empty weapon slots. Raw field 8.2 values are not released.
This module does not emit user UIDs, model IDs or raw field values in diagnostics.
"""
from __future__ import annotations

from .weapon_skin_catalog import known_weapon_part_skin_ids


def validate_weapon_slot_export(
    observed_rows: list[dict],
    observed_meta: dict,
    *,
    weapons: dict,
    attachments: dict,
    weapon_snapshot_ok: bool,
    attachment_snapshot_ok: bool,
) -> tuple[list[dict], dict]:
    metadata = dict(observed_meta)
    metadata.update({
        'schema_version': 4,
        'status': 'not_observed',
        'validated_attachment_references': 0,
        'unknown_attachment_references': 0,
        'conflicting_attachment_references': 0,
        'duplicate_attachment_references': 0,
        'unknown_weapon_references': 0,
        'unmatched_equipped_attachments': 0,
        'csv_release_policy': 'release_only_if_all_observed_links_valid',
        'skin_catalog_total_ids': len(known_weapon_part_skin_ids()),
        'observed_skin_item_ids': 0,
        'catalog_verified_skin_item_ids': 0,
        'unrecognized_skin_values_suppressed': 0,
        'skin_semantic_status': 'unverified_or_unobserved',
    })
    if not observed_meta.get('weapons_with_field8', 0):
        return [], metadata
    if not weapon_snapshot_ok:
        metadata['status'] = 'weapon_snapshot_unavailable'
        return [], metadata
    if not attachment_snapshot_ok:
        metadata['status'] = 'attachment_snapshot_unavailable'
        return [], metadata
    if observed_meta.get('unsupported_weapons'):
        metadata['status'] = 'unsupported_slot_structure'
        return [], metadata

    used = set()
    mismatched = 0
    for row in observed_rows:
        weapon_uid = int(row['weapon_uid'])
        attachment_uid = row.get('attachment_uid')
        if weapon_uid not in weapons:
            metadata['unknown_weapon_references'] += 1
            mismatched += 1
        if attachment_uid in ('', None, 0):
            continue
        attachment_uid = int(attachment_uid)
        if attachment_uid in used:
            metadata['duplicate_attachment_references'] += 1
            mismatched += 1
        used.add(attachment_uid)
        found = attachments.get(attachment_uid)
        if found is None:
            metadata['unknown_attachment_references'] += 1
            mismatched += 1
        elif int(found.get('weapon_uid') or 0) != weapon_uid:
            metadata['conflicting_attachment_references'] += 1
            mismatched += 1
        else:
            metadata['validated_attachment_references'] += 1
    # An equipped attachment without matching slot evidence is not enough to
    # assert that the captured 11021 slot list is complete.
    metadata['unmatched_equipped_attachments'] = sum(
        bool(int(part.get('weapon_uid') or 0)) and uid not in used
        for uid, part in attachments.items()
    )
    if mismatched or metadata['unmatched_equipped_attachments']:
        metadata['status'] = 'link_mismatch'
        return [], metadata

    metadata['status'] = 'validated_observed_links'
    # Internal secondary_raw is never exposed even when its wire structure is
    # recognized. Empty slots retain their positions exactly.
    catalog = known_weapon_part_skin_ids()
    public_rows = []
    observed_skin_values = 0
    verified_skin_values = 0
    for row in observed_rows:
        # Unknown or non-catalog numeric payloads remain private; only IDs
        # from a real static ItemData category are released in the sidecar.
        raw_skin = row.get('secondary_raw', '')
        observed = raw_skin not in ('', None)
        observed_skin_values += int(observed)
        skin_id = int(raw_skin) if observed else 0
        verified = observed and skin_id in catalog
        verified_skin_values += int(verified)
        public_rows.append({k:v for k,v in row.items() if k != 'secondary_raw'} | {
            'attachment_skin_item_id': skin_id if verified else '',
            'attachment_skin_reference_status': ('field_absent' if not observed else 'explicit_zero' if skin_id == 0 else 'catalog_verified' if verified else 'unrecognized'),
        })
    metadata['observed_skin_item_ids'] = observed_skin_values
    metadata['catalog_verified_skin_item_ids'] = verified_skin_values
    metadata['unrecognized_skin_values_suppressed'] = observed_skin_values - verified_skin_values
    if observed_skin_values and verified_skin_values == observed_skin_values:
        metadata['skin_semantic_status'] = 'static_item_category_part_skin_verified'
    elif verified_skin_values:
        metadata['skin_semantic_status'] = 'partly_verified_non_catalog_values_suppressed'
    metadata['observed_positions'] = len(public_rows)
    metadata['secondary_values'] = 0
    metadata['suppressed_unverified_secondary_values'] = True
    return public_rows, metadata
