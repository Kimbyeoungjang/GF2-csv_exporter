from __future__ import annotations

import ctypes
import inspect
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable


FlowKey = tuple[str, int, str, int]

CAPTURE_FILTER = "inbound and tcp and tcp.PayloadLength > 0"
TRACKER_REFRESH_SECONDS = 0.35
MATCH_MISS_REFRESH_SECONDS = 0.10
STATS_INTERVAL_SECONDS = 0.35


def is_windows_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _process_key(value: str) -> str:
    name = Path(str(value or "").strip()).name.casefold()
    return name[:-4] if name.endswith(".exe") else name


@dataclass(slots=True)
class TargetConnectionSnapshot:
    pids: tuple[int, ...] = ()
    process_names: tuple[str, ...] = ()
    inbound_keys: frozenset[FlowKey] = frozenset()
    refreshed_at: float = 0.0

    @property
    def process_found(self) -> bool:
        return bool(self.pids)


class TargetConnectionTracker:
    """Resolve TCP 4-tuples owned by the selected GF2 process.

    WinDivert's NETWORK layer intentionally has packet bytes but no PID.  We
    therefore resolve the process' established TCP endpoints with psutil and
    match sniffed inbound packets by the same 4-tuple.  This lookup is
    read-only and never opens, terminates, suspends, or modifies the process.
    """

    def __init__(self, process_name: str, refresh_interval: float = TRACKER_REFRESH_SECONDS):
        self.process_name = str(process_name or "GF2_Exilium")
        self.refresh_interval = max(0.10, float(refresh_interval))
        self._last_refresh = 0.0
        self._last_match_miss_refresh = 0.0
        self.snapshot = TargetConnectionSnapshot()

    def maybe_refresh(self, *, force: bool = False) -> TargetConnectionSnapshot:
        now = time.monotonic()
        if force or now - self._last_refresh >= self.refresh_interval:
            self.snapshot = self._refresh(now)
            self._last_refresh = now
        return self.snapshot

    def _refresh(self, now: float) -> TargetConnectionSnapshot:
        try:
            import psutil
        except ImportError as exc:  # pragma: no cover - packaging contract
            raise RuntimeError("psutil이 설치되지 않았습니다.") from exc

        target = _process_key(self.process_name)
        pids: list[int] = []
        names: list[str] = []
        keys: set[FlowKey] = set()
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                raw_name = str(proc.info.get("name") or "")
                if _process_key(raw_name) != target:
                    continue
                pid = int(proc.info.get("pid") or proc.pid)
                pids.append(pid)
                names.append(raw_name or str(pid))
                try:
                    conns = proc.net_connections(kind="tcp")
                except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                    continue
                for conn in conns:
                    laddr = getattr(conn, "laddr", None)
                    raddr = getattr(conn, "raddr", None)
                    if not laddr or not raddr:
                        continue
                    lip = str(getattr(laddr, "ip", laddr[0]))
                    lport = int(getattr(laddr, "port", laddr[1]))
                    rip = str(getattr(raddr, "ip", raddr[0]))
                    rport = int(getattr(raddr, "port", raddr[1]))
                    keys.add((lip, lport, rip, rport))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return TargetConnectionSnapshot(
            pids=tuple(sorted(set(pids))),
            process_names=tuple(dict.fromkeys(names)),
            inbound_keys=frozenset(keys),
            refreshed_at=now,
        )

    def matches_inbound(self, packet) -> bool:
        snapshot = self.maybe_refresh()
        key: FlowKey = (
            str(packet.dst_addr or ""),
            int(packet.dst_port or 0),
            str(packet.src_addr or ""),
            int(packet.src_port or 0),
        )
        if key in snapshot.inbound_keys:
            return True
        # A connection can be created between refreshes, but forcing a full
        # process/net_connections scan for every unrelated inbound packet is
        # needlessly expensive. Retry at a short bounded cadence; unmatched
        # packets are also held briefly in memory by PassivePacketCapture and
        # promoted once the flow is confirmed.
        now = time.monotonic()
        if snapshot.process_found and now - self._last_match_miss_refresh >= MATCH_MISS_REFRESH_SECONDS:
            self._last_match_miss_refresh = now
            return key in self.maybe_refresh(force=True).inbound_keys
        return False


@dataclass(slots=True)
class TcpStreamReassembler:
    """Small receive-side TCP reassembler for passive packet copies."""

    expected_seq: int | None = None
    pending: dict[int, bytes] = field(default_factory=dict)
    pending_bytes: int = 0
    max_pending_bytes: int = 2 * 1024 * 1024

    def feed(self, seq: int, payload: bytes) -> bytes:
        if not payload:
            return b""
        seq = int(seq) & 0xFFFFFFFF
        data = bytes(payload)
        if self.expected_seq is None:
            self.expected_seq = seq

        def distance(start: int, expected: int) -> int:
            # Signed TCP sequence-space distance, including 32-bit wrap.
            return ((start - expected + (1 << 31)) & 0xFFFFFFFF) - (1 << 31)

        delta = distance(seq, self.expected_seq)
        if delta < 0:
            overlap = -delta
            if overlap >= len(data):
                return b""
            data = data[overlap:]
            seq = self.expected_seq
        # A segment far beyond the reassembly window cannot bridge a gap.
        elif delta > self.max_pending_bytes:
            return b""
        if len(data) > self.max_pending_bytes:
            return b""
        existing = self.pending.get(seq, b"")
        if len(data) > len(existing):
            self.pending[seq] = data
            self.pending_bytes += len(data) - len(existing)

        out = bytearray()
        while self.pending:
            expected = self.expected_seq
            # The common in-order path performs one dictionary lookup.
            chunk = self.pending.pop(expected, None)
            if chunk is not None:
                self.pending_bytes -= len(chunk)
                out.extend(chunk)
                self.expected_seq = (expected + len(chunk)) & 0xFFFFFFFF
                continue
            # A buffered segment may now overlap the contiguous prefix, not
            # start exactly at expected. Trim bytes already delivered.
            overlaps = [(distance(start, expected), start) for start in self.pending
                        if distance(start, expected) < 0]
            if not overlaps:
                break
            for offset, start in sorted(overlaps):
                chunk = self.pending.pop(start)
                self.pending_bytes -= len(chunk)
                offset = distance(start, self.expected_seq)
                if -offset < len(chunk):
                    tail = chunk[-offset:]
                    out.extend(tail)
                    self.expected_seq = (self.expected_seq + len(tail)) & 0xFFFFFFFF
        if self.pending_bytes > self.max_pending_bytes:
            # Enforce a byte bound, not merely a segment-count bound.
            expected = self.expected_seq
            kept = {}
            size = 0
            for start, chunk in sorted(self.pending.items(), key=lambda x: distance(x[0], expected)):
                if size + len(chunk) > self.max_pending_bytes:
                    break
                kept[start] = chunk
                size += len(chunk)
            self.pending, self.pending_bytes = kept, size
        return bytes(out)


@dataclass(slots=True)
class PassiveCaptureStats:
    packets_seen: int = 0
    packets_matched: int = 0
    packets_deferred: int = 0
    packets_promoted: int = 0
    packets_unmatched_dropped: int = 0
    payload_bytes: int = 0
    active_flows: int = 0
    process_found: bool = False
    connection_count: int = 0

    def as_dict(self) -> dict[str, int | bool]:
        return {
            "packets_seen": self.packets_seen,
            "packets_matched": self.packets_matched,
            "packets_deferred": self.packets_deferred,
            "packets_promoted": self.packets_promoted,
            "packets_unmatched_dropped": self.packets_unmatched_dropped,
            "payload_bytes": self.payload_bytes,
            "active_flows": self.active_flows,
            "process_found": self.process_found,
            "connection_count": self.connection_count,
        }


def passive_capture_preflight(process_name: str = "GF2_Exilium") -> dict[str, object]:
    """Return a read-only environment diagnosis for the GUI.

    This does not open a WinDivert capture handle and does not modify the
    target process or network configuration.
    """
    report: dict[str, object] = {
        "windows": os.name == "nt",
        "admin": is_windows_admin(),
        "process_name": str(process_name or "GF2_Exilium"),
        "pydivert": False,
        "pydivert_version": "",
        "filter_valid": None,
        "filter_error": "",
        "process_found": False,
        "pids": (),
        "connection_count": 0,
    }
    try:
        import pydivert
        from pydivert.consts import Layer
        report["pydivert"] = True
        report["pydivert_version"] = str(getattr(pydivert, "__version__", "unknown"))
        divert_cls = getattr(pydivert, "WinDivert", None) or getattr(pydivert, "Divert", None)
        if divert_cls is not None and hasattr(divert_cls, "check_filter"):
            ok, _pos, message = divert_cls.check_filter(
                CAPTURE_FILTER, layer=Layer.NETWORK
            )
            report["filter_valid"] = bool(ok)
            report["filter_error"] = "" if ok else "invalid_capture_filter"
    except Exception as exc:
        report["filter_error"] = type(exc).__name__
    try:
        snapshot = TargetConnectionTracker(str(process_name or "GF2_Exilium")).maybe_refresh(force=True)
        report["process_found"] = snapshot.process_found
        report["pids"] = snapshot.pids
        report["connection_count"] = len(snapshot.inbound_keys)
    except Exception as exc:
        report["process_error"] = type(exc).__name__
    return report


class PassivePacketCapture:
    """Read copies of inbound TCP packets without proxying GF2 traffic.

    `Flag.SNIFF` is deliberately used so matched packets continue through the
    Windows network stack normally. The capture backend never calls `send()`
    and never modifies/terminates the target process. Process shutdown, when
    explicitly requested after a successful export, is handled separately by
    the elevated helper after capture has already stopped.
    """

    def __init__(
        self,
        process_name: str,
        on_stream_data: Callable[[FlowKey, bytes], None],
        *,
        on_status: Callable[[str], None] | None = None,
        on_stats: Callable[[dict], None] | None = None,
        on_flow_closed: Callable[[FlowKey], None] | None = None,
        on_packet_copy: Callable[[FlowKey, int, bytes], None] | None = None,
        on_ready: Callable[[dict], None] | None = None,
        stop_event: threading.Event | None = None,
        capture_outbound: bool = False,
    ):
        if capture_outbound:
            raise ValueError("Outbound capture is not supported by the public Exporter")
        if _process_key(process_name) != "gf2_exilium":
            raise ValueError("Only GF2_Exilium is supported")
        self.process_name = process_name
        self.on_stream_data = on_stream_data
        self.on_status = on_status or (lambda _text: None)
        self.on_stats = on_stats or (lambda _stats: None)
        self.on_flow_closed = on_flow_closed or (lambda _flow: None)
        self.on_packet_copy = on_packet_copy or (lambda _flow, _seq, _payload: None)
        self.on_ready = on_ready or (lambda _stats: None)
        self.stop_event = stop_event or threading.Event()
        self.tracker = TargetConnectionTracker(process_name)
        self.streams: dict[FlowKey, TcpStreamReassembler] = {}
        self._flow_last_seen: dict[FlowKey, float] = {}
        self._flow_close_grace = 5.0
        self.stats = PassiveCaptureStats()
        self._handle_lock = threading.Lock()
        self._active_handle = None
        self._recv_accepts_timeout: bool | None = None
        # NETWORK packets do not carry a PID. A just-created GF2 TCP flow can
        # produce its first server response a few milliseconds before psutil
        # exposes that 4-tuple. Keep unmatched copies briefly in memory and
        # promote them only if the flow becomes confirmed as GF2.
        self._pending_unmatched: dict[FlowKey, list[tuple[float, int, bytes]]] = {}
        self._pending_unmatched_bytes = 0
        self._pending_grace_seconds = 1.5
        self._pending_max_bytes = 4 * 1024 * 1024
        # Repeated "game not running / connection waiting" messages used to
        # arrive every tracker refresh (~0.35 s) and bury useful log lines.
        # State changes are reported immediately; unchanged states are only
        # reminded occasionally.
        self._tracker_status_key: tuple[object, ...] | None = None
        self._tracker_status_at = 0.0
        self._tracker_status_reminder_seconds = 8.0

    def request_stop(self) -> None:
        """Stop this passive capture without closing the handle from another thread.

        Modern PyDivert receives use a short timeout, so setting stop_event is
        enough.  For legacy recv() implementations that block indefinitely we
        ask WinDivert to shut down the receive side, but deliberately do not
        close the handle here.  The capture thread remains the sole owner that
        closes it, avoiding the double-close race that produced WinError 6.
        """
        self.stop_event.set()
        if self._recv_accepts_timeout is not False:
            return
        with self._handle_lock:
            handle = self._active_handle
        if handle is None:
            return
        try:
            raw_handle = getattr(handle, "_handle", None)
            if raw_handle is None:
                return
            from pydivert import windivert_dll
            shutdown = getattr(windivert_dll, "WinDivertShutdown", None)
            if shutdown is not None:
                # WINDIVERT_SHUTDOWN_RECV = 0x1. This wakes recv without
                # destroying the handle; the owning capture thread closes it.
                shutdown(raw_handle, 0x1)
        except Exception:
            # If a legacy binding exposes neither timeout nor shutdown, the
            # receive loop may take longer to stop, but we still avoid an
            # unsafe cross-thread/double close.
            pass

    def _recv_packet(self, handle, timeout: float):
        """Receive one packet across PyDivert API variants.

        Some PyDivert builds expose recv() without a timeout keyword. Detect
        that once instead of passing an unsupported keyword and crashing the
        capture immediately. Old builds use blocking recv(); request_stop()
        uses receive-side shutdown rather than closing the handle twice.
        """
        if self._recv_accepts_timeout is None:
            try:
                signature = inspect.signature(handle.recv)
                params = signature.parameters.values()
                self._recv_accepts_timeout = (
                    "timeout" in signature.parameters
                    or any(param.kind is inspect.Parameter.VAR_KEYWORD for param in params)
                )
            except (TypeError, ValueError):
                self._recv_accepts_timeout = False

        if self._recv_accepts_timeout:
            try:
                return handle.recv(timeout=timeout)
            except TypeError as exc:
                # Defensive fallback for wrapper methods that advertise a
                # generic signature but still reject this keyword at runtime.
                if "timeout" not in str(exc).casefold():
                    raise
                self._recv_accepts_timeout = False
        return handle.recv()

    def _deliver_matched_packet(self, flow: FlowKey, seq_num: int, payload: bytes) -> None:
        raw = bytes(payload)
        if not raw:
            return
        self.on_packet_copy(flow, seq_num, raw)
        stream = self.streams.setdefault(flow, TcpStreamReassembler())
        self._flow_last_seen[flow] = time.monotonic()
        contiguous = stream.feed(seq_num, raw)
        self.stats.packets_matched += 1
        self.stats.payload_bytes += len(raw)
        self.stats.active_flows = len(self.streams)
        if contiguous:
            self.on_stream_data(flow, contiguous)

    def _defer_unmatched_packet(self, flow: FlowKey, seq_num: int, payload: bytes) -> None:
        raw = bytes(payload)
        if not raw or len(raw) > self._pending_max_bytes:
            self.stats.packets_unmatched_dropped += 1
            return
        now = time.monotonic()
        self._prune_pending(now)
        while self._pending_unmatched_bytes + len(raw) > self._pending_max_bytes and self._pending_unmatched:
            oldest_flow = min(
                self._pending_unmatched,
                key=lambda key: self._pending_unmatched[key][0][0],
            )
            dropped = self._pending_unmatched.pop(oldest_flow)
            self._pending_unmatched_bytes -= sum(len(item[2]) for item in dropped)
            self.stats.packets_unmatched_dropped += len(dropped)
        self._pending_unmatched.setdefault(flow, []).append((now, int(seq_num), raw))
        self._pending_unmatched_bytes += len(raw)
        self.stats.packets_deferred += 1

    def _promote_pending_for_flow(self, flow: FlowKey) -> None:
        pending = self._pending_unmatched.pop(flow, [])
        if not pending:
            return
        self._pending_unmatched_bytes -= sum(len(item[2]) for item in pending)
        # TCP sequence order is enough here; the stream reassembler still
        # handles overlap/retransmission before bytes reach the game framer.
        for _at, seq_num, raw in sorted(pending, key=lambda item: item[1]):
            self.stats.packets_promoted += 1
            self._deliver_matched_packet(flow, seq_num, raw)

    def _promote_pending(self, snapshot: TargetConnectionSnapshot) -> None:
        for flow in list(self._pending_unmatched):
            if flow in snapshot.inbound_keys:
                self._promote_pending_for_flow(flow)
        self._prune_pending(time.monotonic())

    def _prune_pending(self, now: float) -> None:
        cutoff = now - self._pending_grace_seconds
        for flow in list(self._pending_unmatched):
            kept = [item for item in self._pending_unmatched[flow] if item[0] >= cutoff]
            dropped_count = len(self._pending_unmatched[flow]) - len(kept)
            if not dropped_count:
                continue
            before = sum(len(item[2]) for item in self._pending_unmatched[flow])
            after = sum(len(item[2]) for item in kept)
            self._pending_unmatched_bytes -= before - after
            self.stats.packets_unmatched_dropped += dropped_count
            if kept:
                self._pending_unmatched[flow] = kept
            else:
                self._pending_unmatched.pop(flow, None)

    def run(self) -> None:
        if os.name != "nt":
            raise RuntimeError("실시간 패시브 캡처는 Windows에서만 지원합니다.")
        if not is_windows_admin():
            raise PermissionError("패시브 WinDivert 캡처에는 관리자 권한이 필요합니다. 프로그램을 관리자 권한으로 실행하세요.")
        try:
            import pydivert
            from pydivert.consts import Flag
        except ImportError as exc:
            raise RuntimeError("pydivert/WinDivert 캡처 구성요소가 설치되지 않았습니다.") from exc

        divert_cls = getattr(pydivert, "WinDivert", None) or getattr(pydivert, "Divert", None)
        if divert_cls is None:
            raise RuntimeError("pydivert WinDivert 클래스를 찾지 못했습니다.")

        # SNIFF means copy-and-divert: originals are not removed from the
        # network path.  We receive inbound TCP only, then process-filter by
        # current 4-tuples in user space because NETWORK layer has no PID.
        # RECV_ONLY makes the passive contract explicit at the driver handle
        # level as well: this handle cannot be used to inject packets even by
        # mistake.  SNIFF keeps the original packet on its normal network path.
        diverter = divert_cls(
            CAPTURE_FILTER,
            flags=Flag.SNIFF | Flag.RECV_ONLY,
        )
        try:
            with diverter as handle:
                with self._handle_lock:
                    self._active_handle = handle
                # Do not tell the GUI that capture is ready until WinDivert has
                # actually opened successfully. The helper process can exist
                # several moments before this point, and treating process start
                # as capture readiness can miss one-shot login payloads.
                snapshot = self.tracker.maybe_refresh(force=True)
                self._emit_tracker_status(snapshot, first=True)
                self.on_ready(self.stats.as_dict())
                last_status = time.monotonic()
                last_stats = 0.0
                while not self.stop_event.is_set():
                    try:
                        packet = self._recv_packet(handle, 0.25)
                    except TimeoutError:
                        packet = None
                    except (OSError, RuntimeError):
                        if self.stop_event.is_set():
                            break
                        raise
                    if packet is not None:
                        self.stats.packets_seen += 1
                        # Defense in depth: never copy a packet marked outbound.
                        tcp = packet.tcp
                        payload = (bytes(packet.payload or b"")
                                   if tcp is not None and not getattr(packet, "is_outbound", False) else b"")
                        if payload:
                            flow = (str(packet.dst_addr or ""), int(packet.dst_port or 0),
                                    str(packet.src_addr or ""), int(packet.src_port or 0))
                            matched = self.tracker.matches_inbound(packet)
                            seq_num = int(tcp.seq_num)
                            if matched:
                                # If the first response arrived just before the
                                # flow became visible to psutil, replay it now
                                # before processing the current packet.
                                self._promote_pending_for_flow(flow)
                                self._deliver_matched_packet(flow, seq_num, payload)
                            elif self.tracker.snapshot.process_found:
                                # Keep only a short in-memory candidate copy. It
                                # is written to debug output only after this
                                # exact 4-tuple is confirmed as GF2.
                                self._defer_unmatched_packet(flow, seq_num, payload)
                    now = time.monotonic()
                    if now - last_status >= TRACKER_REFRESH_SECONDS:
                        snapshot = self.tracker.maybe_refresh(force=True)
                        self._promote_pending(snapshot)
                        self._prune_closed_flows(snapshot)
                        self._emit_tracker_status(snapshot, first=False)
                        last_status = now
                    if now - last_stats >= STATS_INTERVAL_SECONDS:
                        self.on_stats(self.stats.as_dict())
                        last_stats = now
                self._prune_pending(time.monotonic() + self._pending_grace_seconds + 0.01)
                self.on_status("캡처를 종료했습니다.")
        finally:
            with self._handle_lock:
                self._active_handle = None
        self.on_stats(self.stats.as_dict())


    def _prune_closed_flows(self, snapshot: TargetConnectionSnapshot) -> None:
        """Release receive-side state after a target TCP connection disappears.

        psutil snapshots can briefly omit a connection while Windows is
        updating socket state, so a short grace period avoids flushing a live
        stream because of one transient lookup miss.  Closing a flow only
        flushes this tool's decoder state; it never touches the socket itself.
        """

        now = time.monotonic()
        active = snapshot.inbound_keys
        for flow in list(self.streams):
            if flow in active:
                self._flow_last_seen[flow] = now
                continue
            last_seen = self._flow_last_seen.get(flow, now)
            if now - last_seen < self._flow_close_grace:
                continue
            self.streams.pop(flow, None)
            self._flow_last_seen.pop(flow, None)
            try:
                self.on_flow_closed(flow)
            finally:
                self.stats.active_flows = len(self.streams)

    def _emit_tracker_status(self, snapshot: TargetConnectionSnapshot, *, first: bool) -> None:
        self.stats.process_found = snapshot.process_found
        self.stats.connection_count = len(snapshot.inbound_keys)

        if snapshot.process_found and snapshot.inbound_keys:
            key: tuple[object, ...] = ("connected", snapshot.pids)
            message = f"소녀전선2 연결 확인 · TCP {len(snapshot.inbound_keys)}개"
        elif snapshot.process_found:
            key = ("waiting_connection", snapshot.pids)
            message = "소녀전선2 실행 확인 · 서버 연결 대기 중"
        else:
            key = ("waiting_process",)
            message = "소녀전선2 실행 대기 중"

        now = time.monotonic()
        changed = key != self._tracker_status_key
        reminder_due = now - self._tracker_status_at >= self._tracker_status_reminder_seconds
        if first or changed or reminder_due:
            self.on_status(message)
            self._tracker_status_key = key
            self._tracker_status_at = now
