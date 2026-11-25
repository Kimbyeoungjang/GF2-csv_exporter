"""RC40 export boundaries: 12 account message types by default.

The 12 types are the *existing partially decoded* account domains. This is a
user-facing product scope, not a claim of complete semantic reconstruction.
Other received game messages are eligible only for metadata-only diagnostics;
raw protocol research belongs to a separate private tool.
"""
from __future__ import annotations

from .account_extension import TYPES as EXTENSION_TYPES

CORE_ACCOUNT_TYPES = frozenset({11021, 11061, 11101, 11138, 11163, 11211, 23201})
DEFAULT_EXPORT_TYPES = CORE_ACCOUNT_TYPES | frozenset(EXTENSION_TYPES)
assert len(DEFAULT_EXPORT_TYPES) == 12

# Source-message names are grounded in the main game message-id enum.
TYPE_NAMES = {
    10833: 'MsgScCountSection',
    11021: 'MsgScGunWeapons',
    11061: 'MsgScGunWeaponMods',
    11101: 'MsgScGuns',
    11138: 'MsgScPublicTalentSkillItems',
    11163: 'MsgScGunRemouldingPlugins',
    11211: 'MsgScItems',
    11627: 'MsgScDormInfo',
    22044: 'MsgScSupportRoomInfo',
    22058: 'MsgScSupportTeams',
    22062: 'MsgScSupportChip',
    23201: 'MsgScGetCustomFormation',
}
assert set(TYPE_NAMES) == DEFAULT_EXPORT_TYPES
ACCOUNT_LABELS = {
    10833: '계정 진행도·업적', 11021: '무기', 11061: '무기 파츠',
    11101: '인형', 11138: '공용키', 11163: '리몰딩', 11211: '아이템',
    11627: '숙소·아바타', 22044: '지원소대 설정', 22058: '지원소대 편성',
    22062: '지원소대 소자', 23201: '저장 제대',
}
assert set(ACCOUNT_LABELS) == DEFAULT_EXPORT_TYPES


def account_packet_coverage(payload_counts, decode_errors, core_success, extension_status, *, progress_snapshot_status=None):
    """Metadata only. Never infer semantic completeness from decoder success."""
    rows = []
    for packet_id in sorted(DEFAULT_EXPORT_TYPES):
        count = int(payload_counts.get(packet_id, 0))
        errors = int(decode_errors.get(packet_id, 0))
        core = packet_id in CORE_ACCOUNT_TYPES
        extension_name = EXTENSION_TYPES.get(packet_id)
        ext = extension_status.get(extension_name, 'missing') if extension_name else 'not_applicable'
        if not count:
            state = 'missing'
        elif core and packet_id not in core_success:
            state = 'core_decode_failed'
        elif extension_name and ext != 'observed':
            state = 'extension_' + ext
        elif packet_id == 10833 and progress_snapshot_status not in (None, 'observed'):
            # The 10833 group-50 extension is not proof that the full
            # account-progress CSV decoder succeeded. Both are released in
            # the default backup, so both must be validated.
            state = 'progress_snapshot_' + progress_snapshot_status
        elif errors:
            state = 'partially_decoded_with_errors'
        else:
            state = 'partial_decoder_observed'
        rows.append({
            'packet_id': packet_id,
            'message_name': TYPE_NAMES[packet_id],
            'required_for_auto_stop': core,
            'received_packets': count,
            'core_decoder': ('observed' if packet_id in core_success else ('failed' if count else 'missing')) if core else 'not_applicable',
            'extension_decoder': ext,
            'decode_errors': errors,
            'capture_state': state,
            'semantic_verification': 'incomplete',
        })
    return {
        'schema_version': 1,
        'default_packet_type_count': 12,
        'required_for_auto_stop': sorted(CORE_ACCOUNT_TYPES),
        'optional_packet_types': sorted(DEFAULT_EXPORT_TYPES - CORE_ACCOUNT_TYPES),
        'observed_packet_types': [r['packet_id'] for r in rows if r['received_packets']],
        'missing_packet_types': [r['packet_id'] for r in rows if not r['received_packets']],
        'all_12_observed': all(r['received_packets'] for r in rows),
        'all_12_decoded_without_error': all(r['capture_state'] == 'partial_decoder_observed' for r in rows),
        'all_fields_semantically_verified': False,
        'packets': rows,
    }
