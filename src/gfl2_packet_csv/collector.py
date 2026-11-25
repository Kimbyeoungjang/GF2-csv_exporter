from __future__ import annotations

import csv
import json
import hashlib
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Hashable

from . import __version__
from .atomic_output import atomic_output, write_verified_zip
from .dataset_catalog import build_catalog, CORE_HEADERS
from .key_catalog import project as key_projection, audit as key_audit
from .item_catalog import FIELDS as ITEM_FIELDS, projection as item_projection
from .key_evolution import FIELDS as EVOLUTION_FIELDS, projection as evolution_projection
from .attachment_affixes import FILE as AFFIX_FILE, HEADERS as AFFIX_HEADERS, project_inventory, validate_rows
from .doll_growth_catalog import class_projection, rank_projection
from .weapon_growth_catalog import growth_projection
from .weapon_skin_catalog import known_weapon_skin_ids
from . import additional_doll_details as ADD_DETAILS
from .additional_doll import AdditionalDollObservation, FILE as ADD_DOLL_FILE, HEADERS as ADD_DOLL_HEADERS
from .dorm_costumes import DormCostumeObservation, FILE as DORM_FILE, HEADERS as DORM_HEADERS
from .weapon_slot_observations import FILE as WEAPON_OBS_FILE, HEADERS as WEAPON_OBS_HEADERS, rows_from_unmapped
from .weapon_link_integrity import validate_weapon_slot_export
from .core_field_audit import audit_core_unknown_fields
from .doll_key_order_integrity import audit_doll_key_slots
from .equipment_owner_links import FILE as OWNER_FILE, HEADERS as OWNER_HEADERS, build_owner_links
from .framing import GameMessageFramer, PayloadAssembler
from .wire_probe import WireDecodeError, parse_fields, _varint
from .panel_state import PanelObservations
from .equipment_state import EquipmentObservations
from .account_extension import ExtensionCollector
from .account_csv import serialize, OPTIONAL_FILES, CSV_SCHEMA
from .account_export_scope import DEFAULT_EXPORT_TYPES, CORE_ACCOUNT_TYPES, account_packet_coverage
from .support_integrity import build_support_audit
from .formation_slots import (decode_saved_formations, export_saved_formations,
    FILES as FORMATION_FILES, HEADERS as FORMATION_HEADERS)
from .progress_snapshot import (PROGRESS_TYPE, PROGRESS_FILES, ALL_PROGRESS_FILES, WIRE_FILE, GROUPS_FILE, ENTRIES_FILE, GROUP_HEADERS, ENTRY_HEADERS, ProgressSnapshot, decode_progress_snapshot, validate_projection)

WEAPONS = 11021
ATTACHMENTS = 11061
DOLLS = 11101
COMMON_KEYS = 11138
REMOLDINGS = 11163
ITEMS = 11211
FORMATIONS = 23201

# These wire fields have been observed in real account records, but their
# semantics are not confirmed. Preserve them without assigning game labels.
# Keeping this sidecar separate avoids breaking the established 8 core CSVs.
UNMAPPED_WIRE_FIELDS: dict[int, frozenset[int]] = {
    COMMON_KEYS: frozenset({3, 4}),
    REMOLDINGS: frozenset({4}),
    DOLLS: frozenset({2, 4}),
    WEAPONS: frozenset({7, 8}),
}
UNMAPPED_FIELDS_FILE = "account_unmapped_fields.csv"
MAX_UNMAPPED_FIELD_BYTES = 4096

# Validated through controlled equip/unequip state transitions (research
# snapshots 002->003 and 004->005 for 11138.3; 003->004 for 11163.4).
# An incoming item record with this owner field absent denotes a detached
# item. Do not reuse an older owner when the same record appears again during
# one live capture. Other unmapped fields remain untouched on absence.
OBSERVED_CLEAR_ON_ABSENCE = frozenset({(COMMON_KEYS, 3), (REMOLDINGS, 4)})

KNOWN_ACCOUNT_TYPES = frozenset({
    WEAPONS, ATTACHMENTS, DOLLS, COMMON_KEYS, ITEMS, REMOLDINGS, FORMATIONS,
})

# Snapshot only state the individual decoder is allowed to mutate. The
# protocol can carry multiple records in a single game payload; a malformed
# trailing record must not commit the valid-looking prefix. This is an
# in-memory rollback boundary and does not persist any original packet data.
CORE_TRANSACTION_FIELDS = {
    DOLLS: ('dolls', 'equipment_dolls', 'unmapped_fields'),
    REMOLDINGS: ('remoldings', 'unmapped_fields'),
    FORMATIONS: ('formations', 'saved_formations'),
    WEAPONS: ('weapons', 'unmapped_fields'),
    ATTACHMENTS: ('attachments',),
    COMMON_KEYS: ('common_keys', 'unmapped_fields'),
    ITEMS: ('items',),
}
assert set(CORE_TRANSACTION_FIELDS) == KNOWN_ACCOUNT_TYPES

# Empty repeat payloads do not establish that a previously observed inventory
# has been removed. Keep its observations for partial recovery, but do not
# let the final complete-backup gate treat that ambiguous repeat as success.
# Saved formations have their own explicitly replacing snapshot decoder.
MERGED_INVENTORY_FIELDS = {
    DOLLS: 'dolls', REMOLDINGS: 'remoldings', WEAPONS: 'weapons',
    ATTACHMENTS: 'attachments', COMMON_KEYS: 'common_keys', ITEMS: 'items',
}


def _fields(data: bytes, max_fields: int = 30000):
    return parse_fields(data, max_fields=max_fields)


def _first_int(fields, number: int, default: int = 0) -> int:
    values = [(w, v) for n, w, v in fields if n == number]
    if not values:
        return default
    if len(values) != 1 or values[0][0] != 0:
        raise WireDecodeError("invalid scalar wire shape")
    return int(values[0][1])


def _first_bytes(fields, number: int) -> bytes:
    values = [(w, v) for n, w, v in fields if n == number]
    if not values:
        return b""
    if len(values) != 1 or values[0][0] != 2:
        raise WireDecodeError("invalid singular bytes field")
    return bytes(values[0][1])


def _core_records(data: bytes, record_fields: set[int], *, max_fields=30000, max_record_fields=1000):
    """Validate known repeated record envelopes before yielding any rows.

    Other top-level fields are not interpreted. Nonempty messages containing
    none of the expected record fields are not established empty inventories.
    """
    fields = _fields(data, max_fields=max_fields)
    found = False
    for number, wire, value in fields:
        if number not in record_fields:
            continue
        found = True
        if wire != 2:
            raise WireDecodeError("invalid record envelope")
        record = _fields(bytes(value), max_fields=max_record_fields)
        # All these record types use field 1 as PK except remoldings, which
        # are checked separately (field 3). Field 1 is their model ID.
        yield number, record
    if fields and not found:
        raise WireDecodeError("missing expected inventory records")


def _unique_id(fields, number, seen):
    value = _first_int(fields, number)
    if not value or value in seen:
        raise WireDecodeError("missing or duplicate inventory identifier")
    seen.add(value)
    return value


def _updated_scalar(fields, number, previous, key, default=None):
    return _first_int(fields, number, previous.get(key, default))



def _has_field(fields, number: int) -> bool:
    return any(n == number for n, _w, _value in fields)


def _packed_varints(raw: bytes) -> list[int]:
    """Read an entire protobuf packed uint64 sequence or reject it.

    Previously an incomplete final value (or an eleventh byte) returned an
    apparently valid prefix. Key and weapon references must never be silently
    truncated before they are exported. Zero placeholders remain excluded
    from the legacy inventory projection; their exact positions are retained
    separately in doll_key_values.csv.
    """
    out: list[int] = []
    offset = 0
    while offset < len(raw):
        value, offset = _varint(raw, offset)
        if value:
            out.append(value)
    return out


def _numeric_values(fields, number: int) -> list[int]:
    values: list[int] = []
    for n, w, value in fields:
        if n != number:
            continue
        if w == 0 and int(value) > 0:
            values.append(int(value))
        elif w == 2:
            values.extend(_packed_varints(bytes(value)))
        elif w != 0:
            raise WireDecodeError("invalid numeric array wire shape")
    return sorted(set(values))


@dataclass
class PacketCollector:
    output_dir: Path
    bundle_dir: Path | None = None
    framers: dict[Hashable, GameMessageFramer] = field(default_factory=dict)
    assemblers: dict[Hashable, PayloadAssembler] = field(default_factory=dict)
    dolls: dict[int, dict[str, Any]] = field(default_factory=dict)
    remoldings: dict[str, dict[str, Any]] = field(default_factory=dict)
    formations: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    saved_formations: dict[int, Any] = field(default_factory=dict)
    equipment_dolls: dict[int, dict[str, Any]] = field(default_factory=dict)
    weapons: dict[int, dict[str, Any]] = field(default_factory=dict)
    attachments: dict[int, dict[str, Any]] = field(default_factory=dict)
    common_keys: dict[int, dict[str, Any]] = field(default_factory=dict)
    # (packet type, record UID/model ID, wire field) -> ordered field values.
    unmapped_fields: dict[tuple[int, int, int], list[tuple[int, str]]] = field(default_factory=dict)
    additional_doll: AdditionalDollObservation = field(default_factory=AdditionalDollObservation, repr=False)
    dorm_costumes: DormCostumeObservation = field(default_factory=DormCostumeObservation, repr=False)
    progress_snapshot: ProgressSnapshot | None = None
    progress_status: str = "missing"
    preserve_progress_wire: bool = False
    preserve_all_packets_csv: bool = False
    items: dict[int, dict[str, Any]] = field(default_factory=dict)
    payload_counts: dict[int, int] = field(default_factory=dict)
    on_payload: Callable[[int], None] | None = field(default=None, repr=False)
    on_raw_payload: Callable[[int, bytes], None] | None = field(default=None, repr=False)
    decode_errors: dict[int, int] = field(default_factory=dict)
    core_success_types: set[int] = field(default_factory=set)
    _closed_framing_totals: dict[str, int] = field(default_factory=dict, repr=False)
    _data_lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    panel_observations: PanelObservations = field(default_factory=PanelObservations, repr=False)

    equipment_observations: EquipmentObservations = field(default_factory=EquipmentObservations, repr=False)

    extension: ExtensionCollector = field(default_factory=ExtensionCollector, repr=False)

    def __post_init__(self) -> None:
        if self.preserve_all_packets_csv or self.preserve_progress_wire:
            raise ValueError("배포용 Exporter는 원시 패킷 및 진행 원문 저장을 지원하지 않습니다.")

    def feed(self, content: bytes) -> None:
        self.feed_flow("default", content)

    def feed_flow(self, flow_key: Hashable, content: bytes) -> None:
        framer = self.framers.setdefault(flow_key, GameMessageFramer())
        assembler = self.assemblers.setdefault(flow_key, PayloadAssembler())
        for payload in framer.feed(content):
            for payload_type, data in assembler.feed(payload):
                self.handle_payload(payload_type, data)

    def close_flow(self, flow_key: Hashable) -> None:
        assembler = self.assemblers.pop(flow_key, None)
        framer = self.framers.pop(flow_key, None)
        if framer is not None:
            self._merge_framing_diagnostics(framer.diagnostics())
        if assembler is not None:
            for payload_type, data in assembler.flush():
                self.handle_payload(payload_type, data)

    def flush(self) -> None:
        for flow_key in list(self.assemblers):
            self.close_flow(flow_key)

    def _merge_framing_diagnostics(self, diagnostics: dict[str, Any]) -> None:
        for key, value in diagnostics.items():
            if isinstance(value, bool) or not isinstance(value, int):
                continue
            if key == "max_buffer_seen":
                self._closed_framing_totals[key] = max(self._closed_framing_totals.get(key, 0), value)
            elif key == "buffer_bytes":
                self._closed_framing_totals["closed_tail_buffer_bytes"] = (
                    self._closed_framing_totals.get("closed_tail_buffer_bytes", 0) + value
                )
            else:
                self._closed_framing_totals[key] = self._closed_framing_totals.get(key, 0) + value

    def framing_diagnostics(self) -> dict[str, Any]:
        totals = dict(self._closed_framing_totals)
        active_buffers = 0
        active_synced = 0
        for framer in self.framers.values():
            diag = framer.diagnostics()
            active_buffers += int(diag.get("buffer_bytes", 0) or 0)
            active_synced += int(bool(diag.get("synced")))
            for key, value in diag.items():
                if key in {"buffer_bytes", "synced"} or isinstance(value, bool) or not isinstance(value, int):
                    continue
                if key == "max_buffer_seen":
                    totals[key] = max(totals.get(key, 0), value)
                else:
                    totals[key] = totals.get(key, 0) + value
        totals["active_framers"] = len(self.framers)
        totals["active_buffer_bytes"] = active_buffers
        totals["active_synced_framers"] = active_synced
        return totals

    def handle_payload(self, payload_type: int, data: bytes) -> None:
        raw = bytes(data)
        if self.on_raw_payload is not None:
            self.on_raw_payload(int(payload_type), raw)
        decoded_ok = True
        with self._data_lock:
            self.payload_counts[payload_type] = self.payload_counts.get(payload_type, 0) + 1
            # Core decoders are streaming but each *payload* must commit
            # atomically. A trailing malformed nested record can otherwise
            # leave an incomplete inventory while incrementing decode_errors.
            fields_to_restore = CORE_TRANSACTION_FIELDS.get(payload_type, ())
            # Record dictionaries are replaced, never mutated in place. A shallow
            # map snapshot therefore preserves rollback without copying every
            # nested inventory row on every incoming packet.
            before = {name: getattr(self, name).copy() for name in fields_to_restore}
            try:
                inventory = MERGED_INVENTORY_FIELDS.get(payload_type)
                if not raw and inventory is not None and getattr(self, inventory):
                    raise WireDecodeError("ambiguous empty repeat inventory")
                if payload_type == DOLLS:
                    self._decode_dolls(data)
                elif payload_type == REMOLDINGS:
                    self._decode_remoldings(data)
                elif payload_type == FORMATIONS:
                    self._decode_formations(data)
                elif payload_type == WEAPONS:
                    self._decode_weapons(data)
                elif payload_type == ATTACHMENTS:
                    self._decode_attachments(data)
                elif payload_type == COMMON_KEYS:
                    self._decode_common_keys(data)
                elif payload_type == ITEMS:
                    self._decode_items(data)
            except (WireDecodeError, ValueError, TypeError, KeyError, IndexError, OverflowError):
                decoded_ok = False
                for name, original in before.items():
                    setattr(self, name, original)
                # Schema drift in one packet must not discard already captured
                # backup data. A required type only advances login completion
                # after at least one successful semantic decode.
                self.decode_errors[payload_type] = self.decode_errors.get(payload_type, 0) + 1
            if decoded_ok:
                self.panel_observations.observe(payload_type, raw)
                self.equipment_observations.observe(payload_type, raw)
                if payload_type in CORE_ACCOUNT_TYPES:
                    self.core_success_types.add(payload_type)
                self.extension.observe(payload_type, raw)
                if payload_type == DOLLS:
                    self.additional_doll.observe(raw)
                if payload_type == 11627:
                    self.dorm_costumes.observe(raw)
                    if self.dorm_costumes.status in ("invalid", "conflict"):
                        self.decode_errors[payload_type] = self.decode_errors.get(payload_type, 0) + 1
                if payload_type == PROGRESS_TYPE and self.progress_status not in ("invalid", "conflict"):
                    try:
                        snapshot = decode_progress_snapshot(raw)
                        if self.progress_snapshot is not None and self.progress_snapshot.raw != snapshot.raw:
                            self.progress_snapshot = None
                            self.progress_status = "conflict"
                        else:
                            self.progress_snapshot = snapshot
                            self.progress_status = "observed"
                    except ValueError:
                        self.progress_snapshot = None
                        self.progress_status = "invalid"
        if self.on_payload is not None and (payload_type not in KNOWN_ACCOUNT_TYPES or decoded_ok):
            self.on_payload(payload_type)

    def _preserve_unmapped_scalars(self, packet_type: int, record_id: int, fields) -> None:
        """Preserve selected unknown wire fields without guessing their semantics.

        Most unmapped fields retain prior observations on omission, because
        missing does not generally imply zero. Two equipment owner references
        are confirmed nullable: missing 11138.3 and missing 11163.4 in a
        matching item record mean detached. Clear only those stale references.
        All fields on a record are committed atomically.
        """
        updates: dict[tuple[int, int, int], list[tuple[int, str]]] = {}
        for field_number in UNMAPPED_WIRE_FIELDS.get(packet_type, ()):
            values = []
            for number, wire, value in fields:
                if number != field_number:
                    continue
                if wire == 0:
                    values.append((wire, str(int(value))))
                elif wire in (1, 2, 5):
                    data = bytes(value)
                    if len(data) > MAX_UNMAPPED_FIELD_BYTES:
                        raise WireDecodeError("unmapped field exceeds preservation bound")
                    values.append((wire, data.hex()))
                else:
                    raise WireDecodeError("unknown unmapped field wire type")
            if values:
                updates[(packet_type, record_id, field_number)] = values
            elif (packet_type, field_number) in OBSERVED_CLEAR_ON_ABSENCE:
                self.unmapped_fields.pop((packet_type, record_id, field_number), None)
        self.unmapped_fields.update(updates)

    def _decode_dolls(self, data: bytes) -> None:
        seen = set()
        for _, fields in _core_records(data, {1}, max_record_fields=5000):
            doll_id = _unique_id(fields, 1, seen)
            self._preserve_unmapped_scalars(DOLLS, doll_id, fields)
            previous_doll = self.dolls.get(doll_id, {})
            # Only a currently observed field 31 is projected; no stage is inferred from level.
            level = _updated_scalar(fields, 3, previous_doll, "level")
            stage, stage_status = class_projection(_first_int(fields, 31, None), _first_int(fields, 3, None))
            self.dolls[doll_id] = {
                "doll_id": doll_id,
                "level": level,
                "class_stage": stage,
                "class_stage_reference_status": stage_status,
                "rank": _updated_scalar(fields, 6, previous_doll, "rank"),
            }
            previous_equipment = self.equipment_dolls.get(doll_id, {})
            self.equipment_dolls[doll_id] = {
                "doll_id": doll_id,
                # The observed field is a scalar, not an unordered ID list.
                # In particular an explicit zero must clear the prior weapon.
                "weapon_uid": _updated_scalar(fields, 62, previous_equipment, "weapon_uid"),
                **{key: _numeric_values(fields, number) if _has_field(fields, number) else list(previous_equipment.get(key, []))
                   for key, number in (("fixed_key_ids",42),("common_key_ids",43),
                                       ("common_key_uids",45),("expansion_key_ids",52))},
            }

    def _decode_remoldings(self, data: bytes) -> None:
        seen = set()
        for _, fields in _core_records(data, {1}, max_record_fields=100):
            uid = _unique_id(fields, 3, seen)
            rid = _first_int(fields, 1)
            if not rid:
                raise WireDecodeError("missing remolding model")
            # Repeated uint64 options may use several packed segments or
            # unpacked varints. The extension preserves their exact order.
            for number, wire, content in fields:
                if number != 2 or wire == 0:
                    continue
                if wire != 2:
                    raise WireDecodeError("invalid remolding option wire shape")
                offset = 0
                while offset < len(content):
                    _, offset = _varint(content, offset)
            _first_int(fields, 4)
            self._preserve_unmapped_scalars(REMOLDINGS, uid, fields)
            self.remoldings[str(uid)] = {"uid": str(uid), "remolding_id": rid}

    def _decode_formations(self, data: bytes) -> None:
        ordered = decode_saved_formations(data)
        self.saved_formations = dict(enumerate(ordered, 1))
        # Legacy CSV stays compact and name-keyed for existing importers.
        # Exact source ordering and empty slots are in the new sidecars.
        self.formations = {(name or "이름 없는 제대"): [{"doll_id": slot["doll_id"]} for slot in slots if slot["doll_id"]]
                           for name, slots in ordered}

    def _decode_weapons(self, data: bytes) -> None:
        seen, links = set(), set()
        equipped = {}
        complete_records = True
        previous_ids = set(self.weapons)
        for n, fields in _core_records(data, {1, 2}):
            if n == 1:
                uid = _unique_id(fields, 1, seen)
                previous = self.weapons.get(uid, {})
                model = _updated_scalar(fields, 2, previous, "item_id")
                if not model:
                    raise WireDecodeError("missing weapon model")
                complete_records &= all(_has_field(fields, f) for f in (2,3,6))
                # A changed model invalidates earlier model-dependent observations.
                # Keep the instance's owner reference separate; it is not a model property.
                same_model = previous.get("item_id") == model
                base = previous if same_model else {}
                if previous and not same_model:
                    for field in UNMAPPED_WIRE_FIELDS[WEAPONS]:
                        self.unmapped_fields.pop((WEAPONS, uid, field), None)
                self._preserve_unmapped_scalars(WEAPONS, uid, fields)
                # 11021.5 is a Weapon skin ItemData reference. Its category
                # is verified; active-selection semantics remain unconfirmed.
                # Sparse records may omit fields, so distinguish retained
                # earlier observations from current-record observations.
                if _has_field(fields, 5):
                    candidate = _first_int(fields, 5)
                    skin_status = ('explicit_zero' if candidate == 0 else
                                   'catalog_verified' if candidate in known_weapon_skin_ids() else 'unrecognized')
                    skin_id = candidate if skin_status == 'catalog_verified' else None
                elif all(_has_field(fields, f) for f in (2, 3, 6)) or not previous or model != previous.get("item_id"):
                    skin_id, skin_status = None, 'field_absent'
                else:
                    skin_id = previous.get('weapon_skin_item_id')
                    skin_status = ('retained_catalog_verified' if skin_id is not None else 'field_unobserved')
                self.weapons[uid] = {
                    "uid": uid, "item_id": model,
                    "level": _updated_scalar(fields, 3, base, "level"),
                    "rank": _updated_scalar(fields, 6, base, "rank"),
                    "weapon_skin_item_id": skin_id, "weapon_skin_reference_status": skin_status,
                    "equipped_doll_id": previous.get("equipped_doll_id", 0),
                }
            else:
                uid = _unique_id(fields, 1, links)
                if not _has_field(fields, 2):
                    raise WireDecodeError("missing weapon owner reference")
                equipped[uid] = _first_int(fields, 2)
        # Only clear absent links with a complete-record refresh covering all
        # previous inventory; a sparse change is not a full ownership snapshot.
        if previous_ids and previous_ids <= seen and complete_records:
            for uid in seen:
                self.weapons[uid] = {**self.weapons[uid], "equipped_doll_id": equipped.get(uid, 0)}
        for uid, doll_id in equipped.items():
            if uid not in self.weapons:
                raise WireDecodeError("weapon link without owned record")
            self.weapons[uid] = {**self.weapons[uid], "equipped_doll_id": doll_id}

    def _decode_attachments(self, data: bytes) -> None:
        previous_ids = set(self.attachments)
        snapshot_ids, links = set(), set()
        equipped = {}
        complete_records = True
        for n, fields in _core_records(data, {1, 2}, max_fields=60000):
            if n == 1:
                uid = _unique_id(fields, 1, snapshot_ids)
                previous = self.attachments.get(uid, {})
                part_id = _updated_scalar(fields, 2, previous, "part_id")
                if not part_id:
                    raise WireDecodeError("missing attachment model")
                complete_records &= _has_field(fields, 2)
                # Never transfer model-dependent attributes onto a different model.
                same_model = previous.get("part_id") == part_id
                base = previous if same_model else {}
                record = {
                    "uid": uid, "part_id": part_id,
                    **{key: _updated_scalar(fields, number, base, key)
                       for key, number in (("packed_stats",3),("level_raw",4),("effect_id",5),
                                           ("state_raw",6),("packed_calibration",7))},
                    "weapon_uid": previous.get("weapon_uid", 0),
                }
                # Old calibration words are associated with the old option list.
                # A new list without a new calibration observation cannot establish that relationship.
                invalidated = bool(base.get("calibration_invalidated"))
                if _has_field(fields, 7):
                    invalidated = False
                elif record["packed_stats"] != base.get("packed_stats") and base.get("packed_calibration") is not None:
                    record["packed_calibration"] = None
                    invalidated = True
                if invalidated:
                    record["calibration_invalidated"] = True
                elif not _has_field(fields, 7) and record["packed_calibration"] is not None:
                    record["calibration_retained"] = True
                if not _has_field(fields, 4) and base.get("level_raw") is not None:
                    record["upgrade_retained"] = True
                self.attachments[uid] = record
            else:
                uid = _unique_id(fields, 1, links)
                if not _has_field(fields, 2):
                    raise WireDecodeError("missing attachment owner reference")
                equipped[uid] = _first_int(fields, 2)
        if previous_ids and previous_ids <= snapshot_ids and complete_records:
            for uid in snapshot_ids:
                self.attachments[uid] = {**self.attachments[uid], "weapon_uid": equipped.get(uid, 0)}
        for uid, weapon_uid in equipped.items():
            if uid not in self.attachments:
                raise WireDecodeError("attachment link without owned record")
            self.attachments[uid] = {**self.attachments[uid], "weapon_uid": weapon_uid}

    def _decode_common_keys(self, data: bytes) -> None:
        seen = set()
        for _, fields in _core_records(data, {1}, max_record_fields=100):
            uid = _unique_id(fields, 1, seen)
            key_id = _first_int(fields, 2)
            if not key_id:
                raise WireDecodeError("missing common key model")
            _first_int(fields, 3)
            previous = self.common_keys.get(uid)
            if previous is not None and previous["key_id"] != key_id:
                # Model-specific unknown values cannot be carried into a new model.
                for field in UNMAPPED_WIRE_FIELDS[COMMON_KEYS]:
                    self.unmapped_fields.pop((COMMON_KEYS, uid, field), None)
            self._preserve_unmapped_scalars(COMMON_KEYS, uid, fields)
            self.common_keys[uid] = {"uid": uid, "key_id": key_id, "evolution_candidate": _first_int(fields, 4, None)}

    def _decode_items(self, data: bytes) -> None:
        seen = set()
        for _, fields in _core_records(data, {2}, max_record_fields=100):
            item_id = _unique_id(fields, 1, seen)
            if not _has_field(fields, 2):
                raise WireDecodeError('missing item quantity')
            self.items[item_id] = {"item_id": item_id, "quantity": _first_int(fields, 2)}

    def export(
        self,
        *,
        reason: str = "final",
        bundle_name: str = "gfl2_user_csv_bundle.zip",
        bundle_dir: Path | None = None,
        integrated_states: bool = False,
    ) -> dict[str, Any]:
        with self._data_lock:
            return self._export_locked(reason=reason, bundle_name=bundle_name, bundle_dir=bundle_dir, integrated_states=integrated_states)

    def _export_locked(self, *, reason: str, bundle_name: str, bundle_dir: Path | None, integrated_states: bool = False) -> dict[str, Any]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # Keep the doll-side weapon observation independent of the reverse link.
        doll_rows = [{**row, "weapon_uid": self.equipment_dolls.get(doll_id, {}).get("weapon_uid")}
                     for doll_id, row in sorted(self.dolls.items())]
        for row in doll_rows:
            record, status = rank_projection(row["doll_id"], row["rank"])
            row.update(grade_record_id_candidate=record, rank_reference_status=status)
        self._write_csv("dolls.csv", CORE_HEADERS["dolls.csv"], doll_rows)
        for retired in ("formations.csv", "equipment_dolls.csv", "equipment_user.json"):
            (self.output_dir / retired).unlink(missing_ok=True)
        # Retired byte-fragment legacy export; use remolding_details/options.
        (self.output_dir / 'remoldings.csv').unlink(missing_ok=True)
        formation_files = ()
        formation_meta = {"schema_version": 5, "status": "unavailable"}
        if FORMATIONS in self.core_success_types and not self.decode_errors.get(FORMATIONS):
            available = {kind for kind, packet in (("weapon", WEAPONS), ("attachment", ATTACHMENTS), ("common_key", COMMON_KEYS), ("remolding", REMOLDINGS))
                         if packet in self.core_success_types and not self.decode_errors.get(packet)}
            formation_tables, formation_meta = export_saved_formations(list(self.saved_formations.values()),
                weapons=self.weapons, attachments=self.attachments, common_keys=self.common_keys, remoldings=self.remoldings, available=available)
            for name, rows in formation_tables.items():
                self._write_csv(name, FORMATION_HEADERS[name], rows)
            formation_meta["status"] = "observed"
            formation_files = FORMATION_FILES
        else:
            for name in FORMATION_FILES:
                (self.output_dir / name).unlink(missing_ok=True)
        weapon_rows = []
        doll_slots = {doll_id: row.get("weapon_uid") for doll_id,row in self.equipment_dolls.items()}
        current_claims = {}
        for doll_id, weapon in doll_slots.items():
            if weapon:
                current_claims.setdefault(weapon, set()).add(doll_id)
        both_ready = all(t in self.core_success_types and not self.decode_errors.get(t) for t in (DOLLS, WEAPONS))
        for uid in sorted(self.weapons):
            row = self.weapons[uid]
            owner = row.get("equipped_doll_id")
            claims = current_claims.get(uid, set())
            if not both_ready:
                status = "snapshot_unavailable"
            elif owner and owner not in doll_slots:
                status = "owner_outside_owned_dolls"
            elif owner and claims == {owner}:
                status = "verified"
            elif owner or claims:
                status = "reference_mismatch"
            else:
                status = "no_link_observed"
            level_cap, rank_cap, level_status, rank_status = growth_projection(row["item_id"], row["level"], row["rank"])
            weapon_rows.append({**row, "owner_reference_status":status, "static_level_cap":level_cap, "static_rank_cap":rank_cap, "level_reference_status":level_status, "rank_reference_status":rank_status})
        self._write_csv(
            "weapons.csv",
            CORE_HEADERS["weapons.csv"],
            weapon_rows,
        )
        attachment_rows, affix_rows, affix_meta = project_inventory(
            self.attachments, available=(ATTACHMENTS in self.core_success_types and not self.decode_errors.get(ATTACHMENTS)))
        validate_rows(attachment_rows, affix_rows)
        self._write_csv("attachments.csv", CORE_HEADERS["attachments.csv"], attachment_rows)
        affix_files = ()
        if affix_meta['status'] == 'observed':
            self._write_csv(AFFIX_FILE, AFFIX_HEADERS, affix_rows)
            affix_files = (AFFIX_FILE,)
        else:
            (self.output_dir / AFFIX_FILE).unlink(missing_ok=True)
        key_rows = []
        for k in sorted(self.common_keys):
            row = self.common_keys[k]
            kind, status = key_projection(row["key_id"])
            key_rows.append({"uid":row["uid"], "key_id":row["key_id"], "key_type":kind, "key_type_reference_status":status, **dict(zip(EVOLUTION_FIELDS, evolution_projection(row["key_id"], row.get("evolution_candidate"))))})
        self._write_csv("common_keys.csv", CORE_HEADERS["common_keys.csv"], key_rows)
        self._write_csv("items.csv", CORE_HEADERS["items.csv"], [
            {**self.items[k], **dict(zip(ITEM_FIELDS, item_projection(k)))} for k in sorted(self.items)])
        # Unknown wire fields can include identifiers and opaque contents.
        # They are private research artifacts, never distributable account files.
        # Keep verified inventory-to-attachment relations for GF2Tools, while
        # suppressing the unknown secondary wire value from public backups.
        observed_links, observed_metadata = rows_from_unmapped(self.unmapped_fields)
        weapon_observations, weapon_observation_meta = validate_weapon_slot_export(
            observed_links, observed_metadata,
            weapons=self.weapons, attachments=self.attachments,
            weapon_snapshot_ok=(WEAPONS in self.core_success_types and
                                not self.decode_errors.get(WEAPONS)),
            attachment_snapshot_ok=(ATTACHMENTS in self.core_success_types and
                                    not self.decode_errors.get(ATTACHMENTS)),
        )
        # Non-sensitive reference counts are distinct from claims of complete
        # semantic decoding. These audits never publish unknown field values.
        core_field_audit = audit_core_unknown_fields(
            self.unmapped_fields,
            dolls=self.dolls, common_keys=self.common_keys,
            remoldings=self.remoldings, equipment_dolls=self.equipment_dolls,
            doll_extension=self.extension.chunks.get('dolls'),
            received=set(self.payload_counts),
        )
        (self.output_dir / UNMAPPED_FIELDS_FILE).unlink(missing_ok=True)
        (self.output_dir / WIRE_FILE).unlink(missing_ok=True)
        if weapon_observations:
            self._write_csv(WEAPON_OBS_FILE, WEAPON_OBS_HEADERS, weapon_observations)
            weapon_observation_meta["file"] = WEAPON_OBS_FILE
            weapon_observation_meta["csv_sha256"] = hashlib.sha256(
                (self.output_dir / WEAPON_OBS_FILE).read_bytes()).hexdigest()
        else:
            (self.output_dir / WEAPON_OBS_FILE).unlink(missing_ok=True)
            weapon_observation_meta["file"] = None
        # Structured packet 10833 projection; never write its wire bytes.
        # Keep repeated group IDs and positions. Clear stale files.
        snapshot = self.progress_snapshot if self.progress_status == "observed" else None
        if snapshot is not None:
            progress_entries = snapshot.public_entries()
            validate_projection(snapshot.groups, progress_entries)
            self._write_csv(GROUPS_FILE, list(GROUP_HEADERS), list(snapshot.groups))
            self._write_csv(ENTRIES_FILE, list(ENTRY_HEADERS), progress_entries)
            (self.output_dir / WIRE_FILE).unlink(missing_ok=True)
        else:
            for progress_name in ALL_PROGRESS_FILES:
                (self.output_dir / progress_name).unlink(missing_ok=True)
        progress_metadata = snapshot.manifest(include_wire=False) if snapshot is not None else {
            "schema_version": 1,
            "status": self.progress_status,
            "files": [],
            "semantic_status": "not_interpreted",
        }
        if snapshot is not None:
            # Checksums protect the ordinary (debug-off) structured backup
            # against accidental numeric-value changes. They do not provide
            # authenticity: an attacker could alter both CSV and manifest.
            progress_metadata["schema_version"] = 5
            progress_metadata["csv_sha256"] = {
                name: hashlib.sha256((self.output_dir / name).read_bytes()).hexdigest()
                for name in PROGRESS_FILES
            }
        # Write each captured domain into its own CSV, beside the original CSVs.
        # Old snapshots are cleared, so a missing packet cannot reuse past data.
        extension_status = {}
        extension_export_error = False
        try:
            extension_doc = self.extension.document(
                doll_ids=self.dolls, remolding_uids=self.remoldings)
            extension_status = dict(extension_doc['status'])
            extra_csvs = serialize(extension_doc)
        except (ValueError, TypeError, KeyError, IndexError, OverflowError):
            extra_csvs = {}
            extension_export_error = True
        # Remove the retired RC55 name from a reused output directory.
        (self.output_dir / 'support_unit_extras.csv').unlink(missing_ok=True)
        for extra_name in OPTIONAL_FILES:
            extra_path = self.output_dir / extra_name
            if extra_name in extra_csvs:
                with atomic_output(extra_path) as temporary:
                    temporary.write_bytes(extra_csvs[extra_name])
            else:
                extra_path.unlink(missing_ok=True)
        owner_rows, owner_meta = build_owner_links(
            self.unmapped_fields, common_keys=self.common_keys, remoldings=self.remoldings,
            dolls=self.dolls, doll_extension=(extension_doc['sections'].get('dolls') if not extension_export_error else None),
            available={packet for packet in (DOLLS, COMMON_KEYS, REMOLDINGS)
                       if packet in self.core_success_types and not self.decode_errors.get(packet)})
        owner_files = (OWNER_FILE,) if owner_meta['file'] else ()
        if owner_files:
            self._write_csv(OWNER_FILE, OWNER_HEADERS, owner_rows)
        else:
            (self.output_dir / OWNER_FILE).unlink(missing_ok=True)
        dorm_rows, dorm_meta = self.dorm_costumes.projection(
            dolls=self.dolls, doll_snapshot_ok=(DOLLS in self.core_success_types and not self.decode_errors.get(DOLLS)))
        dorm_files = (DORM_FILE,) if dorm_meta['file'] else ()
        if dorm_files:
            self._write_csv(DORM_FILE, DORM_HEADERS, dorm_rows)
        else:
            (self.output_dir / DORM_FILE).unlink(missing_ok=True)
        additional_rows, additional_meta = self.additional_doll.projection(
            dolls=self.dolls, weapons=self.weapons, owned_claims=current_claims,
            doll_available=(DOLLS in self.core_success_types and not self.decode_errors.get(DOLLS)),
            weapon_available=both_ready)
        additional_files = (ADD_DOLL_FILE,) if additional_rows else ()
        if additional_rows:
            self._write_csv(ADD_DOLL_FILE, ADD_DOLL_HEADERS, additional_rows)
        else:
            (self.output_dir / ADD_DOLL_FILE).unlink(missing_ok=True)
        detail_rows, detail_meta = self.additional_doll.detail_projection(additional_rows, self.common_keys,
            COMMON_KEYS in self.core_success_types and not self.decode_errors.get(COMMON_KEYS))
        for name, headers in ADD_DETAILS.HEADERS.items():
            if name in detail_rows: self._write_csv(name, headers, detail_rows[name])
            else: (self.output_dir / name).unlink(missing_ok=True)
        additional_files += tuple(detail_rows)
        from .reference_data import current
        manifest = {
            "reference_data": current().metadata(),
            "account_data_contract": 35,
            "additional_doll_details": detail_meta,
            "additional_doll_observation": additional_meta,
            "talent_key_reference_audit": key_audit(extension_doc if not extension_export_error else None, self.common_keys, inventory_available=(COMMON_KEYS in self.core_success_types and not self.decode_errors.get(COMMON_KEYS))),
            "attachment_affix_audit": affix_meta,
            "doll_class_reference_counts": {status: sum(r.get("class_stage_reference_status") == status for r in self.dolls.values()) for status in ("catalog_consistent", "field_absent", "explicit_zero", "catalog_unavailable", "unrecognized", "level_unavailable", "level_cap_mismatch")},
            "dorm_costume_observations": dorm_meta,
            "weapon_skin_reference_counts": {status: sum(r["weapon_skin_reference_status"] == status for r in weapon_rows)
                for status in ('catalog_verified', 'retained_catalog_verified', 'explicit_zero', 'field_absent', 'field_unobserved', 'unrecognized')},
            "weapon_owner_reference_counts": {status:sum(r["owner_reference_status"]==status for r in weapon_rows)
                for status in sorted({r["owner_reference_status"] for r in weapon_rows})},
            "current_equipment_owner_links": owner_meta,
            "schema_id": "gfl2-user-csv-backup",
            "schema_version": 1,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "counts": {
                "dolls": len(self.dolls),
                "remoldings": len(self.remoldings),
                "formations": len(self.saved_formations),
                "equipment_dolls": len(self.equipment_dolls),
                "weapons": len(self.weapons),
                "attachments": len(self.attachments),
                "common_keys": len(self.common_keys),
                "items": len(self.items),
            },
            "export_reason": reason if reason in {"final", "login_sync", "login_sync_incomplete", "cancelled_partial", "test", "offline_validation"} else "unclassified",
            "unmapped_wire_fields": {
                "schema_version": 1,
                "file": None,
                "rows": 0,
                "record_groups": 0,
                "packet_types": [],
                "semantic_status": "unconfirmed_raw_fields",
            },
            "progress_snapshot_10833": progress_metadata,
            "weapon_slot_observations": weapon_observation_meta,
            "saved_formation_slots": formation_meta,
            "core_unknown_field_audit": core_field_audit,
            # The public backup includes only the 12 account packet types,
            # independently of debug diagnostic state.
            "payload_counts": {str(k): v for k, v in sorted(self.payload_counts.items())
                               if k in DEFAULT_EXPORT_TYPES},
            "decode_errors": {str(k): v for k, v in sorted(self.decode_errors.items())
                              if k in DEFAULT_EXPORT_TYPES},
            "account_packet_coverage": account_packet_coverage(
                self.payload_counts, self.decode_errors, self.core_success_types,
                extension_status, progress_snapshot_status=self.progress_status),
            "packet_export_scope": "account_12_only",
            # Domain status is metadata only: no UIDs or payload bytes.
            "account_extension_status": extension_status,
            "account_extension_export_error": extension_export_error,
            "support_data_audit": (build_support_audit(extension_doc)
                                   if not extension_export_error else {"schema_version": 1, "status": "audit_unavailable"}),
            "doll_key_slot_integrity": audit_doll_key_slots(
                extension_doc if not extension_export_error else None,
                self.equipment_dolls,
                core_observed=(DOLLS in self.core_success_types and
                               not self.decode_errors.get(DOLLS)),
                csv_exported=('doll_key_values.csv' in extra_csvs)),
        }
        manifest["exporter_version"] = __version__
        if extra_csvs:
            manifest["account_csv_schema"] = CSV_SCHEMA
            manifest["account_csv_files"] = sorted(extra_csvs)
        # A seven-domain transport completion is not a twelve-domain account
        # completion. Never overwrite the previous complete backup when the
        # final export reveals missing or invalid extension/core data.
        if reason == "login_sync" and not manifest["account_packet_coverage"]["all_12_decoded_without_error"]:
            manifest["export_reason"] = "login_sync_incomplete"
            bundle_name = "gfl2_user_csv_partial.zip"
            bundle_dir = self.output_dir
        configured_bundle_dir = bundle_dir if bundle_dir is not None else self.bundle_dir
        bundle_root = (
            Path(configured_bundle_dir).expanduser().resolve()
            if configured_bundle_dir is not None
            else self.output_dir
        )
        bundle_root.mkdir(parents=True, exist_ok=True)
        safe_bundle_name = Path(str(bundle_name or "gfl2_user_csv_bundle.zip")).name
        bundle = bundle_root / safe_bundle_name
        compatible = (
            "dolls.csv", "weapons.csv", "attachments.csv", "common_keys.csv", "items.csv",
            "manifest.json",
        )
        members = compatible + affix_files + formation_files + owner_files + dorm_files + additional_files + tuple(sorted(extra_csvs)) + ((WEAPON_OBS_FILE,) if weapon_observations else ()) + (PROGRESS_FILES if snapshot is not None else ())
        manifest["datasets"] = build_catalog(self.output_dir, members, manifest)
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8')
        with atomic_output(self.output_dir / 'manifest.json') as temporary:
            temporary.write_bytes(manifest_bytes)
        if len(set(members)) != len(members):
            raise ValueError('duplicate export member')
        expected = {name: ((len(manifest_bytes), hashlib.sha256(manifest_bytes).hexdigest())
                           if name == 'manifest.json' else
                           (manifest['datasets'][name]['bytes'], manifest['datasets'][name]['sha256']))
                    for name in members}
        if integrated_states and manifest['export_reason'] == 'login_sync':
            from .embedded_states import write_integrated_backup
            state_status = write_integrated_backup(bundle, self.output_dir, expected, manifest_bytes,
                self.panel_observations, self.equipment_observations, self.dolls, self.weapons, self.attachments)
            manifest.update(state_status)
            manifest['bundle'] = str(bundle)
            return manifest
        write_verified_zip(bundle, self.output_dir, expected)
        manifest["bundle"] = str(bundle)
        # Independent optional sidecar: do not break a successful V35 backup.
        # A stale sidecar cannot bind to a newly published ZIP (SHA256 check).
        if manifest['export_reason'] == 'login_sync':
            try:
                path = self.panel_observations.publish(bundle, self.dolls)
                manifest['panel_state'] = {'status':'written','file':str(path)}
            except (OSError, ValueError):
                manifest['panel_state'] = {'status':'unavailable'}
            try:
                path = self.equipment_observations.publish(bundle, self.weapons, self.attachments)
                manifest['equipment_state'] = {'status':'written','file':str(path)}
            except (OSError, ValueError):
                manifest['equipment_state'] = {'status':'unavailable'}
        return manifest

    def _write_csv(self, name: str, fields: list[str], rows: list[dict[str, Any]]) -> None:
        with atomic_output(self.output_dir / name) as temporary:
            with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
