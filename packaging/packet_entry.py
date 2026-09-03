from __future__ import annotations

import sys
import traceback
from datetime import datetime
from pathlib import Path


def _crash_log_path() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path.cwd()
    log_dir = base / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "startup-crash.log"


def _report_startup_crash(exc: BaseException) -> None:
    log_path = _crash_log_path()
    with log_path.open("a", encoding="utf-8") as fp:
        fp.write("\n" + "=" * 72 + "\n")
        fp.write(datetime.now().astimezone().isoformat(timespec="seconds") + "\n")
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=fp)

    # The packaged app is windowed, so uncaught exceptions would otherwise
    # look like an instant silent exit.  Best-effort native message box keeps
    # diagnostics visible even if Qt itself failed to import/start.
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None,
            f"GFL2 Packet CSV Exporter failed to start.\n\n{type(exc).__name__}: {exc}\n\nLog: {log_path}",
            "GFL2 Packet CSV Exporter - Startup Error",
            0x10,
        )
    except Exception:
        pass


def main() -> int:
    try:
        from gfl2_packet_csv.gui import main as gui_main
        return int(gui_main())
    except BaseException as exc:
        _report_startup_crash(exc)
        return 1


raise SystemExit(main())
