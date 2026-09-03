from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any


class IpcEventWriter:
    def __init__(self, event_path: Path):
        self.event_path = Path(event_path)
        self.event_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def emit(self, event_type: str, **payload: Any) -> None:
        record = {"type": event_type, **payload}
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            with self.event_path.open("a", encoding="utf-8", newline="\n") as fp:
                fp.write(line)
                fp.flush()


def ipc_root() -> Path:
    base = Path(tempfile.gettempdir()) / "gfl2-packet-csv-ipc"
    base.mkdir(parents=True, exist_ok=True)
    return base


def event_path_for(token: str) -> Path:
    return ipc_root() / f"{token}.events.jsonl"


def control_path_for(token: str) -> Path:
    return ipc_root() / f"{token}.control"


def write_control(path: Path, command: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(str(command).strip() + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_control(path: Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def cleanup_stale_ipc(*, max_age_seconds: float = 24 * 60 * 60) -> int:
    """Best-effort cleanup of abandoned per-session IPC files."""
    root = ipc_root()
    cutoff = time.time() - max(60.0, float(max_age_seconds))
    removed = 0
    for path in root.iterdir():
        if not path.is_file():
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except (FileNotFoundError, PermissionError, OSError):
            continue
    return removed
