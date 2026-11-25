"""Public exporter command line: account data + metadata-only optional diagnostics."""
from __future__ import annotations
import argparse
import os
import threading
from pathlib import Path
from .capture import PassivePacketCapture, is_windows_admin
from .collector import PacketCollector
from .debug_policy import optional_debug_recorder
from .session_lock import SessionBusy, capture_lock


def _build_parser():
    parser = argparse.ArgumentParser(description="GF2 passive packet -> CSV backup (public release)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    cap = sub.add_parser("capture", help="capture account data")
    cap.add_argument("--process", default="GF2_Exilium", choices=("GF2_Exilium", "GF2_Exilium.exe"))
    cap.add_argument("--output", default="gfl2_packet_backup")
    cap.add_argument("--debug", action="store_true", help="save non-sensitive diagnostic statistics")
    dec = sub.add_parser("decode-payload", help="decode existing local payload into account CSV")
    dec.add_argument("payload_type", type=int)
    dec.add_argument("input")
    dec.add_argument("--output", default="gfl2_packet_backup")
    dec.add_argument("--debug", action="store_true", help="save non-sensitive diagnostic statistics")
    check = sub.add_parser("check-backup", help="verify local ZIP and optional state CSVs without capture")
    check.add_argument("bundle")
    for command in (cap, dec, check):
        command.add_argument("--reference-index", type=Path, default=None)
    return parser


def _capture(process_name: str, output: Path, *, debug: bool = False) -> int:
    collector = PacketCollector(output)
    diag = optional_debug_recorder(output, enabled=debug)
    if diag:
        collector.on_raw_payload = diag.record_payload
    stop = threading.Event()
    def inbound_only(flow, data):
        if isinstance(flow, tuple) and len(flow) == 5 and flow[0] == "outbound":
            return
        collector.feed_flow(flow, data)
    def packet_copy(flow, seq, data):
        if diag:
            diag.record_packet(flow, seq, data)
    capture = PassivePacketCapture(
        process_name, inbound_only, on_status=print, stop_event=stop,
        capture_outbound=False, on_packet_copy=packet_copy if diag else None,
    )
    try:
        capture.run()
    except KeyboardInterrupt:
        stop.set()
        capture.request_stop()
    except Exception as exc:
        print(f"Capture failed: {type(exc).__name__}")
        return 1
    collector.flush()
    manifest = collector.export(reason="login_sync", integrated_states=True)
    if diag:
        diag.finalize(payload_counts=collector.payload_counts,
                      framing_stats=collector.framing_diagnostics(), reason="capture_finished")
    if not manifest["account_packet_coverage"]["all_12_decoded_without_error"]:
        print("Partial account ZIP:", Path(manifest["bundle"]).name)
        return 8
    print("Account export complete:", manifest["counts"])
    for key in ("panel_state", "equipment_state"):
        print(f"{key}: {manifest.get(key, {}).get('status', 'unavailable')}")
    return 0


def main(argv=None):
    args = _build_parser().parse_args(argv)
    if args.cmd == "check-backup":
        import json
        from .backup_preflight import check_backup, exit_code
        report = check_backup(args.bundle, reference_index=args.reference_index)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return exit_code(report)
    if args.reference_index is not None:
        from .reference_data import load_index, select_process_snapshot
        try:
            select_process_snapshot(load_index(args.reference_index))
        except (OSError, ValueError):
            print("Reference pack validation failed")
            return 8
    output = Path(args.output)
    if args.cmd == "decode-payload":
        try:
            # Offline decoding writes the same working CSV/manifest files as
            # live capture. Serialize both writers using the existing lock.
            with capture_lock():
                collector = PacketCollector(output)
                diag = optional_debug_recorder(output, enabled=args.debug)
                if diag:
                    collector.on_raw_payload = diag.record_payload
                collector.handle_payload(args.payload_type, Path(args.input).read_bytes())
                # One payload is not a complete account snapshot.
                result = collector.export(reason="offline_decode",
                    bundle_name="gfl2_user_csv_partial.zip", bundle_dir=output)
                if diag:
                    diag.finalize(payload_counts=collector.payload_counts, reason="offline_decode")
                print("Partial account ZIP:", Path(result['bundle']).name)
                return 0
        except SessionBusy:
            print("Another capture or offline export is already running.")
            return 3
    if os.name == "nt" and not is_windows_admin():
        print("Run elevated or use the GUI for Windows packet capture.")
        return 2
    try:
        with capture_lock():
            return _capture(args.process, output, debug=args.debug)
    except SessionBusy:
        print("Another capture is already running.")
        return 3

if __name__ == "__main__":
    raise SystemExit(main())
