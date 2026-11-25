"""Conservative structured view of weapon wire field 8.

This is an *observation* view. Nested field 1 is verified against known
attachment UIDs in a captured account. Nested field 2 is catalog-matched to ItemData part-skin IDs. An observed
ID is NOT necessarily proof of an actively equipped cosmetic without a
controlled change, so the public CSV labels it as an observed skin item ID.
Public exports retain only validated attachment UID links; original opaque
values are never written to the public backup.
"""
from __future__ import annotations

from .wire_probe import WireDecodeError, parse_fields

FILE = "weapon_slot_observations.csv"
HEADERS = ["weapon_uid", "wire_position", "attachment_uid", "attachment_skin_item_id", "attachment_skin_reference_status"]


def rows_from_unmapped(unmapped_fields: dict) -> tuple[list[dict], dict]:
    rows: list[dict] = []
    groups = 0
    unsupported = 0
    for (packet, uid, field), values in sorted(unmapped_fields.items()):
        if (packet, field) != (11021, 8):
            continue
        groups += 1
        candidate = []
        try:
            if len(values) > 8:
                raise WireDecodeError("too many observed positions")
            for position, (wire, value) in enumerate(values, start=1):
                if wire != 2 or len(value) > 8192:
                    raise WireDecodeError("unexpected weapon field 8 wire type")
                inner = parse_fields(bytes.fromhex(value), max_fields=16)
                if len(inner) > 2 or any(n not in (1, 2) or w != 0 for n, w, _ in inner):
                    raise WireDecodeError("unsupported nested weapon slot fields")
                if len({n for n, _, _ in inner}) != len(inner):
                    raise WireDecodeError("duplicate nested weapon slot field")
                indexed = {n: int(v) for n, _, v in inner}
                if indexed.get(1, 0) < 0 or indexed.get(2, 0) < 0:
                    raise WireDecodeError("negative nested slot value")
                candidate.append({
                    "weapon_uid": uid,
                    "wire_position": position,
                    "attachment_uid": indexed.get(1, ""),
                    "secondary_raw": indexed.get(2, ""),
                })
        except (ValueError, WireDecodeError, TypeError):
            unsupported += 1
            continue
        rows.extend(candidate)
    return rows, {
        "schema_version": 1,
        "semantic_status": "attachment_uid_verified_secondary_unknown",
        "weapons_with_field8": groups,
        "weapons_with_supported_structure": groups - unsupported,
        "unsupported_weapons": unsupported,
        "observed_positions": len(rows),
        "nonempty_attachment_refs": sum(bool(row["attachment_uid"]) for row in rows),
        "secondary_values": sum(row["secondary_raw"] != "" for row in rows),
    }
