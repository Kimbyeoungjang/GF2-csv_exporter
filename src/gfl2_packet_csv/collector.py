from __future__ import annotations

import csv
import json
import threading
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Hashable

from .framing import GameMessageFramer, PayloadAssembler
from .wire_probe import WireDecodeError, parse_fields

WEAPONS = 11021
ATTACHMENTS = 11061
DOLLS = 11101
COMMON_KEYS = 11138
REMOLDINGS = 11163
ITEMS = 11211
FORMATIONS = 23201

KNOWN_ACCOUNT_TYPES = frozenset({
    WEAPONS, ATTACHMENTS, DOLLS, COMMON_KEYS, ITEMS, REMOLDINGS, FORMATIONS,
})


def _fields(data: bytes, max_fields: int = 30000):
    return parse_fields(data, max_fields=max_fields)


def _varints(fields, number: int) -> list[int]:
    return [int(v) for n, w, v in fields if n == number and w == 0]


def _first_int(fields, number: int, default: int = 0) -> int:
    values = _varints(fields, number)
    return values[0] if values else default


def _first_bytes(fields, number: int) -> bytes:
    return next((bytes(v) for n, w, v in fields if n == number and w == 2), b"")


def _first_text(fields, number: int) -> str:
    raw = _first_bytes(fields, number)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return ""


def _has_field(fields, number: int) -> bool:
    return any(n == number for n, _w, _value in fields)


def _packed_varints(raw: bytes) -> list[int]:
    out: list[int] = []
    offset = 0
    while offset < len(raw):
        value = 0
        shift = 0
        for _ in range(10):
            if offset >= len(raw):
                return out
            b = raw[offset]
            offset += 1
            value |= (b & 0x7F) << shift
            if b < 0x80:
                if value > 0:
                    out.append(value)
                break
            shift += 7
        else:
            return out
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
    return sorted(set(values))


def _remolding_stats(raw: bytes) -> tuple[str, str, str]:
    filtered = [f"{b:02x}" for b in raw if b != 0x01]
    return (
        " ".join(filtered[0:3]),
        " ".join(filtered[3:6]),
        " ".join(filtered[6:9]),
    )


@dataclass
class PacketCollector:
    output_dir: Path
    bundle_dir: Path | None = None
    framers: dict[Hashable, GameMessageFramer] = field(default_factory=dict)
    assemblers: dict[Hashable, PayloadAssembler] = field(default_factory=dict)
    dolls: dict[int, dict[str, Any]] = field(default_factory=dict)
    remoldings: dict[str, dict[str, Any]] = field(default_factory=dict)
    formations: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    equipment_dolls: dict[int, dict[str, Any]] = field(default_factory=dict)
    weapons: dict[int, dict[str, Any]] = field(default_factory=dict)
    attachments: dict[int, dict[str, Any]] = field(default_factory=dict)
    common_keys: dict[int, dict[str, Any]] = field(default_factory=dict)
    items: dict[int, dict[str, Any]] = field(default_factory=dict)
    payload_counts: dict[int, int] = field(default_factory=dict)
    on_payload: Callable[[int], None] | None = field(default=None, repr=False)
    on_raw_payload: Callable[[int, bytes], None] | None = field(default=None, repr=False)
    decode_errors: dict[int, int] = field(default_factory=dict)
    _closed_framing_totals: dict[str, int] = field(default_factory=dict, repr=False)
    _data_lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

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
            try:
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
            except (WireDecodeError, ValueError, TypeError):
                decoded_ok = False
                # Schema drift in one packet must not discard already captured
                # backup data. A required type only advances login completion
                # after at least one successful semantic decode.
                self.decode_errors[payload_type] = self.decode_errors.get(payload_type, 0) + 1
        if self.on_payload is not None and (payload_type not in KNOWN_ACCOUNT_TYPES or decoded_ok):
            self.on_payload(payload_type)

    def _decode_dolls(self, data: bytes) -> None:
        for n, w, raw in _fields(data):
            if n != 1 or w != 2:
                continue
            fields = _fields(bytes(raw), max_fields=5000)
            doll_id = _first_int(fields, 1)
            if doll_id <= 0:
                continue
            previous_doll = self.dolls.get(doll_id, {})
            self.dolls[doll_id] = {
                "doll_id": doll_id,
                "level": _first_int(fields, 3, int(previous_doll.get("level", 0))) if _has_field(fields, 3) else int(previous_doll.get("level", 0)),
                "rank": _first_int(fields, 6, int(previous_doll.get("rank", 0))) if _has_field(fields, 6) else int(previous_doll.get("rank", 0)),
            }
            previous_equipment = self.equipment_dolls.get(doll_id, {})
            weapon_values = _numeric_values(fields, 62) if _has_field(fields, 62) else []
            self.equipment_dolls[doll_id] = {
                "doll_id": doll_id,
                "weapon_uid": (weapon_values or [int(previous_equipment.get("weapon_uid", 0))])[0],
                "fixed_key_ids": _numeric_values(fields, 42) if _has_field(fields, 42) else list(previous_equipment.get("fixed_key_ids", [])),
                "common_key_ids": _numeric_values(fields, 43) if _has_field(fields, 43) else list(previous_equipment.get("common_key_ids", [])),
                "common_key_uids": _numeric_values(fields, 45) if _has_field(fields, 45) else list(previous_equipment.get("common_key_uids", [])),
                "expansion_key_ids": _numeric_values(fields, 52) if _has_field(fields, 52) else list(previous_equipment.get("expansion_key_ids", [])),
            }

    def _decode_remoldings(self, data: bytes) -> None:
        for n, w, raw in _fields(data):
            if n != 1 or w != 2:
                continue
            fields = _fields(bytes(raw), max_fields=100)
            rid = _first_int(fields, 1)
            content = _first_bytes(fields, 2)
            uid = _first_int(fields, 3)
            if uid <= 0:
                continue
            stat1, stat2, stat3 = _remolding_stats(content)
            self.remoldings[str(uid)] = {
                "uid": f"U{uid}",
                "remolding_id": rid,
                "stat1": stat1,
                "stat2": stat2,
                "stat3": stat3,
            }

    def _decode_formations(self, data: bytes) -> None:
        top = _fields(data, max_fields=10000)
        wrapper = _first_bytes(top, 1)
        if not wrapper and data:
            return
        inner = _fields(wrapper, max_fields=10000) if wrapper else []
        for n, w, raw in inner:
            if n != 1 or w != 2:
                continue
            fields = _fields(bytes(raw), max_fields=500)
            name = _first_text(fields, 1) or "이름 없는 제대"
            members: list[dict[str, Any]] = []
            for fn, fw, doll_raw in fields:
                if fn != 2 or fw != 2:
                    continue
                doll_fields = _fields(bytes(doll_raw), max_fields=50)
                doll_id = _first_int(doll_fields, 1)
                if doll_id > 0:
                    members.append({"doll_id": doll_id})
            self.formations[name] = members

    def _decode_weapons(self, data: bytes) -> None:
        equipped: dict[int, int] = {}
        for n, w, raw in _fields(data, max_fields=30000):
            if w != 2:
                continue
            fields = _fields(bytes(raw), max_fields=1000)
            if n == 1:
                uid = _first_int(fields, 1)
                item_id = _first_int(fields, 2)
                if uid <= 0:
                    continue
                candidates: list[int] = []
                for fn, fw, value in fields:
                    if fw != 0 or fn in {1, 3, 6} or int(value) <= 0:
                        continue
                    numeric = int(value)
                    if 100 <= numeric <= 0x7FFFFFFF and numeric not in candidates:
                        candidates.append(numeric)
                if item_id > 0:
                    candidates = [item_id] + [x for x in candidates if x != item_id]
                previous = self.weapons.get(uid, {})
                self.weapons[uid] = {
                    "uid": uid,
                    "item_id": item_id if item_id > 0 else int(previous.get("item_id", 0)),
                    "item_id_candidates": candidates[:16] if candidates else list(previous.get("item_id_candidates", [])),
                    "level": _first_int(fields, 3, int(previous.get("level", 0))) if _has_field(fields, 3) else int(previous.get("level", 0)),
                    "rank": _first_int(fields, 6, int(previous.get("rank", 0))) if _has_field(fields, 6) else int(previous.get("rank", 0)),
                    "equipped_doll_id": int(previous.get("equipped_doll_id", 0)),
                }
            elif n == 2:
                uid = _first_int(fields, 1)
                doll_id = _first_int(fields, 2)
                if uid > 0 and doll_id > 0:
                    equipped[uid] = doll_id
        for uid, doll_id in equipped.items():
            if uid in self.weapons:
                self.weapons[uid]["equipped_doll_id"] = doll_id

    def _decode_attachments(self, data: bytes) -> None:
        equipped: dict[int, int] = {}
        for n, w, raw in _fields(data, max_fields=60000):
            if w != 2:
                continue
            fields = _fields(bytes(raw), max_fields=1000)
            if n == 1:
                uid = _first_int(fields, 1)
                part_id = _first_int(fields, 2)
                if uid <= 0:
                    continue
                previous = self.attachments.get(uid, {})
                self.attachments[uid] = {
                    "uid": uid,
                    "part_id": part_id if part_id > 0 else int(previous.get("part_id", 0)),
                    "packed_stats": _first_int(fields, 3, int(previous.get("packed_stats", 0))) if _has_field(fields, 3) else int(previous.get("packed_stats", 0)),
                    "level_raw": _first_int(fields, 4, int(previous.get("level_raw", 0))) if _has_field(fields, 4) else int(previous.get("level_raw", 0)),
                    "effect_id": _first_int(fields, 5, int(previous.get("effect_id", 0))) if _has_field(fields, 5) else int(previous.get("effect_id", 0)),
                    "state_raw": _first_int(fields, 6, int(previous.get("state_raw", 0))) if _has_field(fields, 6) else int(previous.get("state_raw", 0)),
                    "packed_calibration": _first_int(fields, 7, int(previous.get("packed_calibration", 0))) if _has_field(fields, 7) else int(previous.get("packed_calibration", 0)),
                    "weapon_uid": int(previous.get("weapon_uid", 0)),
                }
            elif n == 2:
                uid = _first_int(fields, 1)
                weapon_uid = _first_int(fields, 2)
                if uid > 0 and weapon_uid > 0:
                    equipped[uid] = weapon_uid
        for uid, weapon_uid in equipped.items():
            if uid in self.attachments:
                self.attachments[uid]["weapon_uid"] = weapon_uid

    def _decode_common_keys(self, data: bytes) -> None:
        for n, w, raw in _fields(data, max_fields=30000):
            if n != 1 or w != 2:
                continue
            fields = _fields(bytes(raw), max_fields=100)
            uid = _first_int(fields, 1)
            key_id = _first_int(fields, 2)
            if uid <= 0 or key_id <= 0:
                continue
            self.common_keys[uid] = {"uid": uid, "key_id": key_id}

    def _decode_items(self, data: bytes) -> None:
        for n, w, raw in _fields(data, max_fields=30000):
            if n != 2 or w != 2:
                continue
            fields = _fields(bytes(raw), max_fields=100)
            item_id = _first_int(fields, 1)
            if item_id <= 0:
                continue
            self.items[item_id] = {
                "item_id": item_id,
                "quantity": _first_int(fields, 2),
            }

    def export(
        self,
        *,
        reason: str = "final",
        bundle_name: str = "gfl2_user_csv_bundle.zip",
        bundle_dir: Path | None = None,
    ) -> dict[str, Any]:
        with self._data_lock:
            return self._export_locked(reason=reason, bundle_name=bundle_name, bundle_dir=bundle_dir)

    def _export_locked(self, *, reason: str, bundle_name: str, bundle_dir: Path | None) -> dict[str, Any]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._write_csv("dolls.csv", ["doll_id", "level", "rank"], [self.dolls[k] for k in sorted(self.dolls)])
        self._write_csv(
            "remoldings.csv", ["uid", "stat1", "stat2", "stat3"],
            [{k: self.remoldings[uid][k] for k in ("uid", "stat1", "stat2", "stat3")} for uid in sorted(self.remoldings)],
        )
        formation_rows = []
        for name in sorted(self.formations):
            members = self.formations[name]
            if not members:
                formation_rows.append({"formation_name": name, "position": 0, "doll_id": "", "doll_name": ""})
            for position, member in enumerate(members, 1):
                formation_rows.append({"formation_name": name, "position": position, "doll_id": member["doll_id"], "doll_name": ""})
        self._write_csv("formations.csv", ["formation_name", "position", "doll_id", "doll_name"], formation_rows)
        equipment_rows = []
        for doll_id in sorted(self.equipment_dolls):
            row = self.equipment_dolls[doll_id]
            equipment_rows.append({
                "doll_id": row["doll_id"],
                "weapon_uid": row["weapon_uid"],
                "fixed_key_ids": " ".join(map(str, row["fixed_key_ids"])),
                "common_key_ids": " ".join(map(str, row["common_key_ids"])),
                "common_key_uids": " ".join(map(str, row["common_key_uids"])),
                "expansion_key_ids": " ".join(map(str, row["expansion_key_ids"])),
            })
        self._write_csv(
            "equipment_dolls.csv",
            ["doll_id", "weapon_uid", "fixed_key_ids", "common_key_ids", "common_key_uids", "expansion_key_ids"],
            equipment_rows,
        )
        weapon_rows = []
        for uid in sorted(self.weapons):
            row = self.weapons[uid]
            weapon_rows.append({**row, "item_id_candidates": " ".join(map(str, row["item_id_candidates"]))})
        self._write_csv(
            "weapons.csv",
            ["uid", "item_id", "item_id_candidates", "level", "rank", "equipped_doll_id"],
            weapon_rows,
        )
        self._write_csv(
            "attachments.csv",
            ["uid", "part_id", "packed_stats", "level_raw", "effect_id", "state_raw", "packed_calibration", "weapon_uid"],
            [self.attachments[k] for k in sorted(self.attachments)],
        )
        self._write_csv("common_keys.csv", ["uid", "key_id"], [self.common_keys[k] for k in sorted(self.common_keys)])
        self._write_csv("items.csv", ["item_id", "quantity"], [self.items[k] for k in sorted(self.items)])
        sidecar = {
            "schema": 1,
            "dolls": {
                str(k): {name: value for name, value in row.items() if name != "doll_id"}
                for k, row in self.equipment_dolls.items()
            },
            "weapons_by_uid": {str(k): row for k, row in self.weapons.items()},
        }
        (self.output_dir / "equipment_user.json").write_text(json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest = {
            "schema_id": "gfl2-user-csv-backup",
            "schema_version": 1,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "counts": {
                "dolls": len(self.dolls),
                "remoldings": len(self.remoldings),
                "formations": len(self.formations),
                "equipment_dolls": len(self.equipment_dolls),
                "weapons": len(self.weapons),
                "attachments": len(self.attachments),
                "common_keys": len(self.common_keys),
                "items": len(self.items),
            },
            "export_reason": reason,
            "payload_counts": {str(k): v for k, v in sorted(self.payload_counts.items())},
            "decode_errors": {str(k): v for k, v in sorted(self.decode_errors.items())},
        }
        (self.output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
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
            "dolls.csv", "remoldings.csv", "formations.csv",
            "equipment_dolls.csv", "weapons.csv", "attachments.csv", "common_keys.csv", "items.csv",
            "equipment_user.json", "manifest.json",
        )
        tmp_bundle = bundle.with_suffix(bundle.suffix + ".tmp")
        tmp_bundle.unlink(missing_ok=True)
        with zipfile.ZipFile(tmp_bundle, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for name in compatible:
                path = self.output_dir / name
                if path.is_file():
                    zf.write(path, name)
        tmp_bundle.replace(bundle)
        manifest["bundle"] = str(bundle)
        return manifest

    def _write_csv(self, name: str, fields: list[str], rows: list[dict[str, Any]]) -> None:
        with (self.output_dir / name).open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
