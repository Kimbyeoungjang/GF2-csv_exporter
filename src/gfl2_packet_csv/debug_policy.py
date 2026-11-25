"""Explicit consent for optional capture diagnostics.

Normal account CSV backups are always written; diagnostics are separate and
never initialized, recorded, or finalized unless requested for this session.
"""
from __future__ import annotations
from pathlib import Path
from .diagnostics import SanitizedDebugRecorder


def optional_debug_recorder(output_dir: Path, *, enabled: bool) -> SanitizedDebugRecorder | None:
    if type(enabled) is not bool:
        raise TypeError("diagnostic consent must be a boolean")
    return SanitizedDebugRecorder(output_dir) if enabled else None
