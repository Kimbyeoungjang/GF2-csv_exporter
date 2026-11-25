"""Account-only, value-free support-domain completeness audit.

This intentionally never emits support identifiers, chip UIDs or any raw
packet fields. It distinguishes absent packets from an observed empty list and
records where per-unit level cannot be established from the observed schema.
"""
from __future__ import annotations

from typing import Any
from collections import Counter
from .support_catalog import reference_status, skill_group_status, frame_slot_id, room_level_status


def build_support_audit(document: dict[str, Any]) -> dict[str, Any]:
    sections = document['sections']
    statuses = document['status']
    formations = sections.get('support_formations')
    chips = sections.get('support_chips')
    owned = sections.get('support_unit_ids')

    result: dict[str, Any] = {
        'schema_version': 2,
        'section_status': {k: statuses.get(k, 'missing') for k in
                           ('support_formations', 'support_unit_ids', 'support_chips')},
        'room_level_present': formations['level'] is not None if formations is not None else None,
        # A unit's individual level is not declared by the currently known
        # 22044/22058/22062 wire shape. Do not copy the room's shared level.
        'individual_unit_level': 'not_verified',
        'formation_count': len(formations['formations']) if formations is not None else None,
        'unit_slot_count': None,
        'populated_unit_slots': None,
        'owned_unit_positions': len(owned) if owned is not None else None,
        'unique_owned_unit_ids': len(set(owned)) if owned is not None else None,
        'deployed_unit_ids_not_in_owned': None,
        'normal_chip_count': len(chips['normal']) if chips is not None else None,
        'special_chip_count': len(chips['special']) if chips is not None else None,
        'special_chip_amplification_known_count': (
            sum(x['current_player_amplification_level'] is not None
                for x in chips['special'].values()) if chips is not None else None),
        'normal_chip_slot_references': None,
        'special_chip_slot_references': None,
        'normal_chip_slot_unmatched': None,
        'special_chip_slot_unmatched': None,
        'chip_reference_check': 'not_checked',
        'static_reference_counts': {
            'owned_units': dict(Counter(reference_status('support_unit', x) for x in owned)) if owned is not None else None,
            'normal_chip_models': dict(Counter(reference_status('normal', x['model_id']) for x in chips['normal'].values())) if chips is not None else None,
            'special_chip_models': dict(Counter(reference_status('special', x['model_id']) for x in chips['special'].values())) if chips is not None else None,
            'room_level': room_level_status(formations['level']) if formations is not None else None,
            'unit_skill_groups': None, 'unit_ammunition': None, 'room_ammunition': None,
            'attributes': None, 'normal_frame_slots': None,
        },
    }
    if formations is None:
        return result

    fs = formations['formations']
    units = [u for f in fs for u in f['units']]
    deployed = [u['unit_id'] for u in units if u is not None and u['unit_id']]
    result['unit_slot_count'] = len(units)
    result['populated_unit_slots'] = len(deployed)
    counts = result['static_reference_counts']
    counts['unit_skill_groups'] = dict(Counter(skill_group_status(u['unit_id'], u['skill_id']) for u in units if u is not None))
    counts['unit_ammunition'] = dict(Counter(reference_status('support_ammunition', x) for u in units if u is not None for x in u['ammunition_ids']))
    counts['room_ammunition'] = dict(Counter(reference_status('support_ammunition', x) for x in formations['ammunition_ids']))
    counts['attributes'] = dict(Counter(reference_status('support_attribute', x['attribute_id']) for f in fs for x in f['attributes'].values()))
    counts['normal_frame_slots'] = dict(Counter('catalog_consistent' if frame_slot_id(frame['frame_id'], i) is not None else 'unrecognized_or_unavailable' for f in fs for frame in f['frames'].values() for i in range(1, len(frame['slots'])+1)))
    if owned is not None:
        owned_ids = set(owned)
        result['deployed_unit_ids_not_in_owned'] = sum(unit_id not in owned_ids for unit_id in deployed)

    normal_refs = [uid for f in fs for frame in f['frames'].values()
                   for uid in frame['slots'] if uid is not None]
    special_refs = [uid for f in fs for uid in (f['special_slots'] or []) if uid is not None]
    result['normal_chip_slot_references'] = len(normal_refs)
    result['special_chip_slot_references'] = len(special_refs)
    if chips is not None:
        normal_ids, special_ids = set(chips['normal']), set(chips['special'])
        missing_normal = sum(uid not in normal_ids for uid in normal_refs)
        missing_special = sum(uid not in special_ids for uid in special_refs)
        result['normal_chip_slot_unmatched'] = missing_normal
        result['special_chip_slot_unmatched'] = missing_special
        result['chip_reference_check'] = 'matched' if not missing_normal and not missing_special else 'unmatched'
    return result
