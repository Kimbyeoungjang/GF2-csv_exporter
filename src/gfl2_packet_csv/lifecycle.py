from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from .collector import KNOWN_ACCOUNT_TYPES
from .account_extension import TYPES as EXTENSION_TYPES


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
    # A short, bounded observation interval after the seven mandatory domains.
    # The five additional domains may arrive later. Full-backup callers
    # wait for all of them; legacy callers can retain the early-stop policy.
    optional_settle_seconds: float = 5.0
    optional_types: frozenset[int] = frozenset(EXTENSION_TYPES).difference(KNOWN_ACCOUNT_TYPES)
    incomplete_timeout_seconds: float = 60.0
    required_types: frozenset[int] = KNOWN_ACCOUNT_TYPES
    mode: str = "login_sync"
    seen_types: set[int] = field(default_factory=set)
    started_at: float | None = None
    last_known_at: float | None = None
    last_known_total: int = 0
    last_optional_total: int = 0
    last_optional_at: float | None = None
    all_required_at: float | None = None
    completed_at_deadline: bool = False
    seen_optional: set[int] = field(default_factory=set)
    # Full-backup callers wait for all 12 observed domains before early stop.
    wait_for_optional: bool = False

    def observe(self, payload_counts: Mapping[int, int], now: float) -> str | None:
        """Observe counters and return one terminal lifecycle event.

        Events:
        - ``login_complete``: all required types + quiet window (and, when
          configured, all additional types), or deadline with required types.
          This is a stop signal, not a final export validation result.
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
        optional_total = sum(int(payload_counts.get(t, 0) or 0) for t in self.optional_types)
        if optional_total > self.last_optional_total:
            self.last_optional_at = now
        self.last_optional_total = optional_total
        self.seen_optional.update(t for t in self.optional_types if int(payload_counts.get(t, 0) or 0) > 0)
        if self.required_types.issubset(self.seen_types) and self.all_required_at is None:
            self.all_required_at = now

        if self.last_known_at is None:
            if self.started_at is not None and now - self.started_at >= self.incomplete_timeout_seconds:
                self.mode = "incomplete"
                return "login_incomplete"
            return None

        # Allow late optional extension messages to settle without demanding
        # that any optional message be present. Required and optional message
        # activity both restart the quiet period. Unrelated game traffic does
        # not extend the window indefinitely.
        last_relevant = max(self.last_known_at, self.last_optional_at if self.last_optional_at is not None else self.last_known_at)
        quiet_for = now - last_relevant
        settled = (self.all_required_at is not None and
                   now - self.all_required_at >= self.optional_settle_seconds)
        optional_ready = not self.wait_for_optional or not self.missing_optional_types
        if settled and optional_ready and quiet_for >= self.quiet_seconds:
            self.mode = "complete"
            return "login_complete"

        # The incomplete timeout is a hard upper bound. Unknown/known traffic
        # may continue indefinitely after login, so requiring another quiet
        # window here can leave a 5/7 or 6/7 capture running forever.
        if self.started_at is not None and now - self.started_at >= self.incomplete_timeout_seconds:
            if self.required_types.issubset(self.seen_types):
                # All essential domains exist. Do not discard a valid backup
                # solely because optional traffic prevented a quiet window.
                self.completed_at_deadline = True
                self.mode = "complete"
                return "login_complete"
            self.mode = "incomplete"
            return "login_incomplete"
        return None

    @property
    def missing_optional_types(self) -> tuple[int, ...]:
        return tuple(sorted(self.optional_types.difference(self.seen_optional)))

    @property
    def missing_types(self) -> tuple[int, ...]:
        return tuple(sorted(self.required_types.difference(self.seen_types)))

    @property
    def progress(self) -> tuple[int, int]:
        return len(self.seen_types.intersection(self.required_types)), len(self.required_types)


# Backward-compatible import name used by earlier tests/releases.
LoginSyncWatchState = LoginSyncState
