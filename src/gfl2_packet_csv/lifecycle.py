from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from .collector import KNOWN_ACCOUNT_TYPES


@dataclass
class LoginSyncState:
    """Track a finite login snapshot capture.

    A successful capture completes only after every required account payload
    type has been observed and the known-payload stream has been quiet for a
    short window. The bounded login window starts with capture itself, so even
    a session that yields no recognized payload cannot run forever. If the window expires first, the capture ends
    as incomplete instead of silently switching into a long-running watch
    mode.
    """

    quiet_seconds: float = 3.0
    incomplete_timeout_seconds: float = 60.0
    required_types: frozenset[int] = KNOWN_ACCOUNT_TYPES
    mode: str = "login_sync"
    seen_types: set[int] = field(default_factory=set)
    started_at: float | None = None
    last_known_at: float | None = None
    last_known_total: int = 0

    def observe(self, payload_counts: Mapping[int, int], now: float) -> str | None:
        """Observe counters and return one terminal lifecycle event.

        Events:
        - ``login_complete``: all required types + quiet window
        - ``login_incomplete``: hard login window expired
        """

        if self.mode != "login_sync":
            return None

        if self.started_at is None:
            self.started_at = now

        known_total = sum(int(payload_counts.get(t, 0) or 0) for t in self.required_types)
        current_seen = {t for t in self.required_types if int(payload_counts.get(t, 0) or 0) > 0}

        if known_total > self.last_known_total:
            self.last_known_at = now
        self.last_known_total = known_total
        self.seen_types.update(current_seen)

        if self.last_known_at is None:
            if self.started_at is not None and now - self.started_at >= self.incomplete_timeout_seconds:
                self.mode = "incomplete"
                return "login_incomplete"
            return None

        quiet_for = now - self.last_known_at
        if self.required_types.issubset(self.seen_types) and quiet_for >= self.quiet_seconds:
            self.mode = "complete"
            return "login_complete"

        # The incomplete timeout is a hard upper bound. Unknown/known traffic
        # may continue indefinitely after login, so requiring another quiet
        # window here can leave a 5/7 or 6/7 capture running forever.
        if self.started_at is not None and now - self.started_at >= self.incomplete_timeout_seconds:
            self.mode = "incomplete"
            return "login_incomplete"
        return None

    @property
    def missing_types(self) -> tuple[int, ...]:
        return tuple(sorted(self.required_types.difference(self.seen_types)))

    @property
    def progress(self) -> tuple[int, int]:
        return len(self.seen_types.intersection(self.required_types)), len(self.required_types)


# Backward-compatible import name used by earlier tests/releases.
LoginSyncWatchState = LoginSyncState
