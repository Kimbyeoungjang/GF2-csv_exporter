from __future__ import annotations

import argparse
import os
import threading
from pathlib import Path

from .capture import PassivePacketCapture, is_windows_admin
from .collector import PacketCollector


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GF2 passive packet -> CSV archive/backup exporter")
    sub = parser.add_subparsers(dest="cmd", required=True)

    capture = sub.add_parser("capture", help="passively sniff copies of inbound GF2 packets and write CSV backup files")
    capture.add_argument("--process", default="GF2_Exilium", help="target process name")
    capture.add_argument("--output", default="gfl2_packet_backup", help="output directory")

    decode = sub.add_parser("decode-payload", help="decode one already extracted payload binary")
    decode.add_argument("payload_type", type=int)
    decode.add_argument("input")
    decode.add_argument("--output", default="gfl2_packet_backup")
    return parser


def _capture(process_name: str, output: Path) -> int:
    collector = PacketCollector(output)
    stop = threading.Event()
    capture = PassivePacketCapture(
        process_name,
        lambda flow, data: collector.feed_flow(flow, data),
        on_status=print,
        stop_event=stop,
    )
    print("Passive SNIFF capture. Press Ctrl+C to stop.")
    try:
        capture.run()
    except KeyboardInterrupt:
        stop.set()
        capture.request_stop()
    except Exception as exc:
        print(f"Capture failed: {type(exc).__name__}: {exc}")
        return 1

    collector.flush()
    manifest = collector.export()
    print("Export complete:", manifest["counts"])
    print("Compatible ZIP:", manifest.get("bundle", ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    output = Path(args.output)
    if args.cmd == "decode-payload":
        collector = PacketCollector(output)
        collector.handle_payload(args.payload_type, Path(args.input).read_bytes())
        print(collector.export())
        return 0

    if os.name == "nt" and not is_windows_admin():
        print(
            "CLI capture is not auto-elevated. Use the GUI so the dedicated "
            "windows-packet-capture.exe helper can request administrator rights, "
            "or start this CLI from an already elevated terminal."
        )
        return 2
    return _capture(args.process, output)


if __name__ == "__main__":
    raise SystemExit(main())
