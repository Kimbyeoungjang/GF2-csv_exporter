"""Reconstruct original strategy sequence without inferring battle behavior."""
from __future__ import annotations


def ordered_variants(setting):
    """Return the received strategy sequence from a validated doll setting.

    The active selection is a decoder projection; its wire position is kept
    independently of the order of inactive strategies. No game execution.
    """
    if setting is None:
        return None
    count = setting['variant_count']
    position = setting['active_variant_position']
    saved = list(setting.get('saved_variants_raw', []))
    if count == 0 and position is None and not saved and not setting['entries'] and setting['submode_raw'] is None:
        return []
    if type(position) is not int or not 1 <= position <= count or count != len(saved) + 1 or count > 16:
        raise ValueError('invalid strategy position/count')
    saved.insert(position - 1, {'submode_raw': setting['submode_raw'], 'entries': setting['entries']})
    return saved


def movement_projection(mode):
    """Controlled Carolric movement changes 0x0303 -> 0x0203 -> 0x0103."""
    if mode is None:
        return None, 'field_absent'
    if type(mode) is not int or not 0 <= mode <= 0xffff:
        return None, 'unrecognized_mode'
    code = mode >> 8
    if (mode & 255) not in (1, 2, 3) or code not in (1, 2, 3):
        return None, 'unrecognized_mode'
    return code, {1:'balance_controlled', 2:'defense_controlled', 3:'attack_controlled'}[code]


def conditional_directive_status(mode):
    """Code 3 is the time-sequence setting in the controlled UI observation.

    Other low-byte codes have not been matched to their UI labels.
    """
    if mode is None:
        return 'field_absent'
    if movement_projection(mode)[0] is None:
        return 'unrecognized_mode'
    return 'time_sequence_observed' if mode & 255 == 3 else 'unconfirmed_code'
