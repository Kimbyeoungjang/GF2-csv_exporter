from __future__ import annotations


def main() -> int:
    from gfl2_packet_csv.helper import main as helper_main
    return int(helper_main())


raise SystemExit(main())
