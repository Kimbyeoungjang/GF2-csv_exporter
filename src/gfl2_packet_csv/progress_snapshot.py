"""Structured 10833 projection. Raw bytes stay in memory for duplicate detection.

The public account ZIP never contains wire bytes; non-achievement group
semantics are unresolved. Debug does not include these CSV values.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .account_extension import MAX_BYTES, parse, scalar
from .achievement_catalog import challenge_status
from .progress_catalog import reference_projection
from collections import Counter

PROGRESS_TYPE = 10833
WIRE_FILE = "account_progress_10833.bin"
GROUPS_FILE = "account_progress_groups.csv"
ENTRIES_FILE = "account_progress_entries.csv"
PROGRESS_FILES = (GROUPS_FILE, ENTRIES_FILE)
ALL_PROGRESS_FILES = (WIRE_FILE, GROUPS_FILE, ENTRIES_FILE)
GROUP_HEADERS = ("group_position", "group_id_raw", "field4_count", "field5_count", "field6_count", "other_fields_count")
ENTRY_HEADERS = ("group_position", "group_id_raw", "source_field", "entry_position", "value1_raw", "value2_raw", "decoded_pair", "semantic_status", "reference_catalog_candidate", "reference_status", "value2_observation_status")
MAX_GROUPS = 4096
MAX_ENTRIES = 100_000


@dataclass(frozen=True)
class ProgressSnapshot:
    raw: bytes
    groups: tuple[dict, ...]
    entries: tuple[dict, ...]

    def public_entries(self):
        """Keep research-only timestamp candidates out of the account CSV.

        Only observed binary state codes are projected for other pairs.
        Out-of-domain values stay in memory; absence, malformed fields and
        withholding are independent of the wire pair-completeness flag.
        """
        rows = []
        for entry in self.entries:
            r = dict(entry)
            parse_status = r.pop('value2_parse_status')
            value = r['value2_raw']
            group50_extra = r['group_id_raw'] == 50 and r['source_field'] == 6
            if parse_status != 'observed':
                r['value2_observation_status'] = parse_status
            elif group50_extra:
                r['value2_observation_status'] = 'withheld_group50_additional'
            elif value in (0, 1):
                r['value2_observation_status'] = 'binary_code_observed'
            else:
                r['value2_observation_status'] = 'withheld_outside_observed_domain'
            if r['value2_observation_status'] != 'binary_code_observed':
                r['value2_raw'] = None
            if group50_extra:
                r['semantic_status'] = 'group50_value_withheld_pending_semantics'
            elif r['group_id_raw'] == 50 and r['source_field'] == 4:
                r['semantic_status'] = 'challenge_state_code_unconfirmed'
            else:
                r['semantic_status'] = 'structural_only'
            r['reference_catalog_candidate'], r['reference_status'] = reference_projection(
                r['group_id_raw'], r['source_field'], r['value1_raw'])
            rows.append(r)
        return rows

    def reference_audit(self):
        counts = {}
        for r in self.public_entries():
            name = r['reference_catalog_candidate']
            if name is not None:
                key = f"{r['group_id_raw']}.{r['source_field']}"
                counts.setdefault(key, Counter())[r['reference_status']] += 1
        return {k:dict(v) for k,v in sorted(counts.items())}

    def challenge_audit(self):
        state = [e for e in self.entries if e['group_id_raw'] == 50 and e['source_field'] == 4]
        additional = [e for e in self.entries if e['group_id_raw'] == 50 and e['source_field'] == 6]
        a = {e['value1_raw'] for e in state if e['value1_raw'] is not None}
        b = {e['value1_raw'] for e in additional if e['value1_raw'] is not None}
        # A timestamp-shaped numeric range is evidence only, not an assignment
        # of meaning; no individual timestamp or account identifier is emitted.
        return {
            'state_rows': len(state), 'additional_rows': len(additional),
            'challenge_catalog_matches': sum(challenge_status(e['value1_raw']) == 'catalog_consistent' for e in state),
            'additional_catalog_matches': sum(challenge_status(e['value1_raw']) == 'catalog_consistent' for e in additional),
            'unique_keys_match': bool(state and additional) and len(a)==len(state) and len(b)==len(additional) and a==b,
            'additional_timestamp_shaped_count': sum(isinstance(e['value2_raw'],int) and 1577836800 <= e['value2_raw'] < 1893456000 for e in additional),
            'additional_values_withheld': sum(e['value2_raw'] is not None for e in additional),
            'state_semantics': 'unconfirmed', 'additional_semantics': 'unconfirmed',
        }

    def manifest(self, *, include_wire: bool = False) -> dict:
        return {
            "schema_version": 1,
            "status": "observed",
            "group_count": len(self.groups),
            "entry_count": len(self.entries),
            "group50_field4_count": sum(int(r["field4_count"]) for r in self.groups if r["group_id_raw"] == 50),
            "wire_sha256": hashlib.sha256(self.raw).hexdigest() if include_wire else None,
            "files": list(ALL_PROGRESS_FILES if include_wire else PROGRESS_FILES),
            "unparsed_field_occurrences": sum(int(x["other_fields_count"]) for x in self.groups) + sum(1 for x in self.entries if not x["decoded_pair"]),
            "semantic_status": "group50_challenge_ids_verified_state_codes_and_other_groups_unconfirmed",
            "challenge_audit": self.challenge_audit(),
            "reference_candidate_audit": self.reference_audit(),
            "value2_observation_counts": dict(sorted(Counter(
                r['value2_observation_status'] for r in self.public_entries()).items())),
        }


def decode_progress_snapshot(raw: bytes) -> ProgressSnapshot:
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        raise ValueError("invalid progress snapshot size")
    outer = parse(raw)
    if not outer or set(outer) != {1}:
        raise ValueError("unexpected top-level progress fields")
    blobs = outer[1]
    if not 1 <= len(blobs) <= MAX_GROUPS or any(w != 2 for w, _ in blobs):
        raise ValueError("unexpected progress group encoding")
    groups: list[dict] = []
    entries: list[dict] = []
    for position, (_w, group_blob) in enumerate(blobs, 1):
        group = parse(group_blob)
        group_id = scalar(group, 1)
        if group_id is not None and group_id > 2**64 - 1:
            raise ValueError("invalid progress group ID")
        count_by_field = {4: 0, 5: 0, 6: 0}
        other_count = 0
        for field, values in group.items():
            if field == 1:
                continue
            for wire, record_blob in values:
                if field not in count_by_field or wire != 2:
                    other_count += 1
                    continue
                count_by_field[field] += 1
                if len(entries) >= MAX_ENTRIES:
                    raise ValueError("too many progress records")
                # Nested two-number shape is observed across fields 4/5/6 in
                # the reference captures, not a claim about game semantics.
                try:
                    record = parse(record_blob)
                    # Retain independently readable fields even if a future
                    # packet adds unknown fields or one known field is absent.
                    # decoded_pair=0 still warns that the complete two-field
                    # shape has NOT been established; no defaults are invented.
                    try:
                        val1 = scalar(record, 1)
                    except ValueError:
                        val1 = None
                    try:
                        val2 = scalar(record, 2)
                        value2_parse_status = 'field_absent' if val2 is None else 'observed'
                    except ValueError:
                        val2 = None
                        value2_parse_status = 'invalid_field'
                    recognized = (set(record) == {1, 2}
                                  and val1 is not None and val2 is not None)
                except ValueError:
                    val1 = val2 = None
                    value2_parse_status = 'invalid_record'
                    recognized = False
                entries.append({
                    "group_position": position,
                    "group_id_raw": group_id,
                    "source_field": field,
                    "entry_position": count_by_field[field],
                    "value1_raw": val1,
                    "value2_raw": val2,
                    "value2_parse_status": value2_parse_status,
                    "decoded_pair": int(recognized),
                })
        groups.append({
            "group_position": position,
            "group_id_raw": group_id,
            "field4_count": count_by_field[4],
            "field5_count": count_by_field[5],
            "field6_count": count_by_field[6],
            "other_fields_count": other_count,
        })
    return ProgressSnapshot(raw, tuple(groups), tuple(entries))


def validate_projection(groups, entries):
    """Validate exported progress CSV dictionaries before GF2Tools import.

    Checks positional joins and catalog annotations, not authenticity or
    achievement completion semantics. Accepts typed or CSV string values.
    """
    def uint(v, optional=False):
        if optional and v in (None, ''):
            return None
        if type(v) is int:
            n = v
        elif isinstance(v,str) and v.isascii() and v.isdecimal() and len(v) <= 20:
            n = int(v)
        else:
            raise ValueError('invalid progress numeric field')
        if not 0 <= n <= 2**64 - 1:
            raise ValueError('progress numeric field out of range')
        return n

    indexed = {}
    expected = {}
    for pos,g in enumerate(groups,1):
        if pos > MAX_GROUPS or uint(g['group_position']) != pos:
            raise ValueError('invalid progress group position')
        indexed[pos] = uint(g['group_id_raw'], True)
        for field in (4,5,6):
            expected[pos,field] = uint(g[f'field{field}_count'])
        uint(g['other_fields_count'])
    if not indexed or sum(expected.values()) > MAX_ENTRIES:
        raise ValueError('invalid progress group or entry count')
    seen = set()
    for index,r in enumerate(entries,1):
        if index > MAX_ENTRIES:
            raise ValueError('too many progress entries')
        pos,field,ep = (uint(r[k]) for k in ('group_position','source_field','entry_position'))
        if pos not in indexed or field not in (4,5,6) or not 1 <= ep <= expected[pos,field] or (pos,field,ep) in seen:
            raise ValueError('invalid progress entry position')
        seen.add((pos,field,ep))
        group_id = uint(r['group_id_raw'], True)
        if group_id != indexed[pos]:
            raise ValueError('progress group ID mismatch')
        v1,v2 = uint(r['value1_raw'],True),uint(r['value2_raw'],True)
        pair = uint(r['decoded_pair'])
        if pair not in (0,1):
            raise ValueError('invalid progress pair status')
        withheld = group_id == 50 and field == 6
        semantic = ('group50_value_withheld_pending_semantics' if withheld else
                    'challenge_state_code_unconfirmed' if group_id == 50 and field == 4 else 'structural_only')
        if r['semantic_status'] != semantic or (withheld and v2 is not None):
            raise ValueError('invalid progress semantic projection')
        observation = r['value2_observation_status']
        if observation == 'binary_code_observed':
            if withheld or v2 not in (0, 1):
                raise ValueError('invalid binary progress state')
        elif observation == 'withheld_group50_additional':
            if not withheld or v2 is not None:
                raise ValueError('invalid withheld additional state')
        elif observation == 'withheld_outside_observed_domain':
            if withheld or v2 is not None:
                raise ValueError('invalid withheld progress state')
        elif observation in ('field_absent', 'invalid_field', 'invalid_record'):
            if v2 is not None or pair or (observation == 'invalid_record' and v1 is not None):
                raise ValueError('invalid absent or malformed progress state')
        else:
            raise ValueError('unknown progress observation status')
        if pair and v1 is None:
            raise ValueError('incomplete decoded progress pair')
        name,status = reference_projection(group_id,field,v1)
        if (r['reference_catalog_candidate'] or None, r['reference_status']) != (name,status):
            raise ValueError('progress reference projection mismatch')
    if len(seen) != sum(expected.values()):
        raise ValueError('missing progress entries')
