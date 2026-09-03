from __future__ import annotations

from pathlib import Path

from gfl2_packet_csv.collector import (
    ATTACHMENTS,
    COMMON_KEYS,
    DOLLS,
    FORMATIONS,
    ITEMS,
    KNOWN_ACCOUNT_TYPES,
    REMOLDINGS,
    WEAPONS,
    PacketCollector,
)
from gfl2_packet_csv.lifecycle import LoginSyncState


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _v(field: int, value: int) -> bytes:
    return _varint((field << 3) | 0) + _varint(value)


def _b(field: int, value: bytes) -> bytes:
    return _varint((field << 3) | 2) + _varint(len(value)) + value


def test_known_login_sync_payload_set_is_seven_types() -> None:
    assert KNOWN_ACCOUNT_TYPES == {
        WEAPONS, ATTACHMENTS, DOLLS, COMMON_KEYS, ITEMS, REMOLDINGS, FORMATIONS
    }


def test_added_decoders_export_csv_files(tmp_path: Path) -> None:
    collector = PacketCollector(tmp_path)

    attachment = _v(1, 77) + _v(2, 90131343) + _v(3, 1234) + _v(7, 5678)
    attachment_map = _v(1, 77) + _v(2, 9)
    collector.handle_payload(ATTACHMENTS, _b(1, attachment) + _b(2, attachment_map))

    common_key = _v(1, 1842534124280107008) + _v(2, 91004)
    collector.handle_payload(COMMON_KEYS, _b(1, common_key))

    item = _v(1, 300009) + _v(2, 18)
    zero_item = _v(1, 300091) + _v(2, 0)
    collector.handle_payload(ITEMS, _b(2, item) + _b(2, zero_item))

    manifest = collector.export(reason="test")
    assert collector.attachments[77]["weapon_uid"] == 9
    assert collector.common_keys[1842534124280107008]["key_id"] == 91004
    assert collector.items[300009]["quantity"] == 18
    assert collector.items[300091]["quantity"] == 0
    assert (tmp_path / "attachments.csv").is_file()
    assert (tmp_path / "common_keys.csv").is_file()
    assert (tmp_path / "items.csv").is_file()
    assert manifest["counts"]["attachments"] == 1
    assert manifest["counts"]["common_keys"] == 1
    assert manifest["counts"]["items"] == 2
    assert manifest["export_reason"] == "test"


def test_login_sync_completes_after_all_seven_and_quiet_window() -> None:
    state = LoginSyncState(quiet_seconds=3.0, incomplete_timeout_seconds=60.0)
    counts = {ptype: 1 for ptype in KNOWN_ACCOUNT_TYPES}
    assert state.observe(counts, 10.0) is None
    assert state.observe(counts, 12.9) is None
    assert state.observe(counts, 13.0) == "login_complete"
    assert state.mode == "complete"
    assert state.observe(counts, 20.0) is None


def test_login_sync_timeout_is_terminal_incomplete_not_watch() -> None:
    state = LoginSyncState(quiet_seconds=3.0, incomplete_timeout_seconds=10.0)
    counts = {DOLLS: 1}
    assert state.observe(counts, 100.0) is None
    assert state.observe(counts, 109.9) is None
    assert state.observe(counts, 110.0) == "login_incomplete"
    assert state.mode == "incomplete"
    assert DOLLS not in state.missing_types
    assert state.observe(counts, 120.0) is None



def test_login_sync_timeout_even_when_no_known_payload_arrives() -> None:
    state = LoginSyncState(quiet_seconds=3.0, incomplete_timeout_seconds=5.0)
    assert state.observe({}, 10.0) is None
    assert state.observe({}, 14.9) is None
    assert state.observe({}, 15.0) == "login_incomplete"
    assert state.mode == "incomplete"

def test_partial_weapon_update_preserves_existing_fields(tmp_path: Path) -> None:
    collector = PacketCollector(tmp_path)
    full = _v(1, 55) + _v(2, 123456) + _v(3, 60) + _v(6, 5)
    equipped = _v(1, 55) + _v(2, 1001)
    collector.handle_payload(WEAPONS, _b(1, full) + _b(2, equipped))

    partial = _v(1, 55) + _v(3, 61)
    collector.handle_payload(WEAPONS, _b(1, partial))

    row = collector.weapons[55]
    assert row["item_id"] == 123456
    assert row["level"] == 61
    assert row["rank"] == 5
    assert row["equipped_doll_id"] == 1001


def test_incomplete_timeout_is_hard_even_if_known_payload_keeps_arriving() -> None:
    state = LoginSyncState(quiet_seconds=3.0, incomplete_timeout_seconds=10.0)
    counts = {DOLLS: 1}
    assert state.observe(counts, 100.0) is None
    # Simulate known traffic continuing right up to the deadline.
    counts[DOLLS] = 2
    assert state.observe(counts, 109.9) is None
    counts[DOLLS] = 3
    assert state.observe(counts, 110.0) == "login_incomplete"
    assert state.mode == "incomplete"


def test_bundle_can_be_written_outside_work_directory(tmp_path: Path) -> None:
    work = tmp_path / "gfl2_packet_backup"
    app_root = tmp_path / "app"
    collector = PacketCollector(work, bundle_dir=app_root)
    collector.handle_payload(ITEMS, _b(2, _v(1, 300009) + _v(2, 18)))
    manifest = collector.export(reason="test_bundle_root")

    assert (work / "items.csv").is_file()
    assert not (work / "gfl2_user_csv_bundle.zip").exists()
    assert (app_root / "gfl2_user_csv_bundle.zip").is_file()
    assert Path(manifest["bundle"]) == (app_root / "gfl2_user_csv_bundle.zip").resolve()


def test_required_type_advances_progress_only_after_successful_decode(tmp_path: Path) -> None:
    collector = PacketCollector(tmp_path)
    observed: list[int] = []
    collector.on_payload = observed.append

    # Truncated protobuf: transport/framing saw the type, but semantic decode
    # failed, so login completion must not treat DOLLS as successfully collected.
    collector.handle_payload(DOLLS, b"\x80")
    assert collector.payload_counts[DOLLS] == 1
    assert collector.decode_errors[DOLLS] == 1
    assert observed == []

    doll = _v(1, 1001) + _v(3, 60) + _v(6, 5)
    collector.handle_payload(DOLLS, _b(1, doll))
    assert observed == [DOLLS]
    assert collector.dolls[1001]["level"] == 60


def test_export_replaces_bundle_atomically_after_temp_zip_is_complete(tmp_path: Path) -> None:
    work = tmp_path / "work"
    bundle_root = tmp_path / "root"
    bundle_root.mkdir(parents=True)
    old_bundle = bundle_root / "gfl2_user_csv_bundle.zip"
    old_bundle.write_bytes(b"old-valid-bundle")

    collector = PacketCollector(work, bundle_dir=bundle_root)
    collector.handle_payload(ITEMS, _b(2, _v(1, 300009) + _v(2, 18)))
    collector.export(reason="atomic")

    assert old_bundle.read_bytes() != b"old-valid-bundle"
    assert not (bundle_root / "gfl2_user_csv_bundle.zip.tmp").exists()


def test_partial_export_does_not_overwrite_normal_root_bundle(tmp_path: Path) -> None:
    work = tmp_path / "gfl2_packet_backup"
    root = tmp_path / "app"
    root.mkdir(parents=True)
    full = root / "gfl2_user_csv_bundle.zip"
    full.write_bytes(b"previous-complete-backup")

    collector = PacketCollector(work, bundle_dir=root)
    collector.handle_payload(ITEMS, _b(2, _v(1, 300009) + _v(2, 18)))
    manifest = collector.export(
        reason="login_sync_incomplete",
        bundle_name="gfl2_user_csv_partial.zip",
        bundle_dir=work,
    )

    assert full.read_bytes() == b"previous-complete-backup"
    assert (work / "gfl2_user_csv_partial.zip").is_file()
    assert Path(manifest["bundle"]) == (work / "gfl2_user_csv_partial.zip").resolve()
