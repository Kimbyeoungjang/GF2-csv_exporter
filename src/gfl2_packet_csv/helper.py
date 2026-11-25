from __future__ import annotations

import argparse
import os
import threading
import time
from pathlib import Path

from .capture import PassivePacketCapture, is_windows_admin
from .collector import PacketCollector, KNOWN_ACCOUNT_TYPES, DOLLS, REMOLDINGS, FORMATIONS, WEAPONS, ATTACHMENTS, COMMON_KEYS, ITEMS
from .account_export_scope import DEFAULT_EXPORT_TYPES, ACCOUNT_LABELS
from .debug_policy import optional_debug_recorder
from .ipc import IpcEventWriter, read_control
from .lifecycle import LoginSyncState
from .session_lock import SessionBusy, capture_lock, owner_alive


def _collector_counts(collector: PacketCollector) -> dict[str, int]:
    return {
        "weapons": len(collector.weapons),
        "attachments": len(collector.attachments),
        "dolls": len(collector.dolls),
        "common_keys": len(collector.common_keys),
        "items": len(collector.items),
        "remoldings": len(collector.remoldings),
        "formations": len(collector.saved_formations),
    }


def _process_key(value: str) -> str:
    name = Path(str(value or "").strip()).name.casefold()
    return name[:-4] if name.endswith(".exe") else name


def terminate_target_processes(process_name: str, writer: IpcEventWriter) -> dict[str, object]:
    """Terminate only processes whose executable name exactly matches target.

    This is deliberately isolated from the packet-capture backend: no network
    packet or socket is modified to close the game. The process is terminated
    through the normal Windows process API exposed by psutil after the user has
    been informed that capture is complete.
    """

    import psutil

    target = _process_key(process_name)
    if target != "gf2_exilium":
        raise ValueError("Only GF2_Exilium may be terminated")
    matched = []
    terminated = []
    killed = []
    errors = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = str(proc.info.get("name") or "")
            if _process_key(name) != target:
                continue
            matched.append(int(proc.pid))
            proc.terminate()
            terminated.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError) as exc:
            errors.append(type(exc).__name__)

    if terminated:
        _gone, alive = psutil.wait_procs(terminated, timeout=5.0)
        for proc in alive:
            try:
                proc.kill()
                killed.append(int(proc.pid))
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError) as exc:
                errors.append(type(exc).__name__)

    result = {
        "matched_count": len(matched),
        "terminated_count": sum(p.pid not in killed for p in terminated),
        "killed_count": len(killed),
        "errors": errors,
    }
    writer.emit("target_terminated", **result)
    return result


_CATEGORY_LABELS = ACCOUNT_LABELS


def run_helper(process_name: str, output_dir: Path, bundle_dir: Path, event_path: Path, control_path: Path, *, save_debug: bool = False, full_packet_csv: bool = False, owner_lease: Path | None = None) -> int:
    if _process_key(process_name) != "gf2_exilium":
        raise ValueError("Only GF2_Exilium is supported")
    if full_packet_csv:
        raise ValueError("배포용 Exporter에서 전체 패킷 수집은 허용되지 않습니다.")
    try:
        with capture_lock():
            return _run_helper_locked(process_name, output_dir, bundle_dir, event_path, control_path,
                                      save_debug=save_debug, owner_lease=owner_lease)
    except SessionBusy:
        IpcEventWriter(event_path).emit("error", message="다른 캡처가 실행 중입니다.")
        return 10


def _run_helper_locked(process_name: str, output_dir: Path, bundle_dir: Path, event_path: Path, control_path: Path, *, save_debug: bool, owner_lease: Path | None) -> int:
    writer = IpcEventWriter(event_path)
    writer.emit("helper_started", admin=is_windows_admin())

    if os.name != "nt":
        writer.emit("error", message="실시간 캡처는 Windows에서만 지원합니다.")
        return 2
    if not is_windows_admin():
        writer.emit("error", message="관리자 캡처 도구가 관리자 권한으로 실행되지 않았습니다.")
        return 3

    if not owner_alive(owner_lease):
        writer.emit("cancelled", manifest={})
        writer.emit("helper_exit", reason="owner_exit")
        return 0

    output_dir = Path(output_dir).expanduser().resolve()
    bundle_dir = Path(bundle_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    collector = PacketCollector(output_dir, bundle_dir=bundle_dir)
    writer.emit("log", message="[준비] 게임 연결 및 계정 데이터 12종 수집 대기")
    writer.emit("log", message="[설정] 선택적 디버그 정보: " + ("저장" if save_debug else "저장 안 함"))
    debug_recorder = optional_debug_recorder(output_dir, enabled=save_debug)
    lifecycle = LoginSyncState(wait_for_optional=True)
    observed_counts: dict[int, int] = {}
    observed_lock = threading.Lock()
    stop_event = threading.Event()
    terminal_lock = threading.Lock()
    terminal: dict[str, object] = {"reason": ""}
    last_net_stats: dict[str, object] = {}
    capture_ready_event = threading.Event()

    def observed_snapshot() -> dict[int, int]:
        with observed_lock:
            return dict(observed_counts)

    def emit_stats(net: dict | None = None) -> None:
        nonlocal last_net_stats
        if net is not None:
            last_net_stats = dict(net)
        progress, required = lifecycle.progress
        writer.emit(
            "stats",
            **_collector_counts(collector),
            **last_net_stats,
            sync_seen=progress,
            sync_required=required,
            sync_missing=list(lifecycle.missing_types),
            sync_optional_seen=len(lifecycle.seen_optional),
            sync_optional_total=len(lifecycle.optional_types),
            sync_account_seen=sum(t in DEFAULT_EXPORT_TYPES for t in observed_snapshot()),
        )

    observed_required: set[int] = set()

    def on_payload(payload_type: int) -> None:
        with observed_lock:
            observed_counts[payload_type] = observed_counts.get(payload_type, 0) + 1
            first_required = payload_type in DEFAULT_EXPORT_TYPES and payload_type not in observed_required
            if first_required:
                observed_required.add(payload_type)
                seen = len(observed_required)
            else:
                seen = 0
        if first_required:
            writer.emit("account_section", label=_CATEGORY_LABELS[payload_type],
                        completed=seen, total=len(DEFAULT_EXPORT_TYPES))
        emit_stats()

    collector.on_payload = on_payload
    if debug_recorder is not None:
        collector.on_raw_payload = debug_recorder.record_payload

    def on_capture_ready(stats: dict) -> None:
        capture_ready_event.set()
        writer.emit("capture_ready", **dict(stats or {}))

    def on_stream_data(flow, data: bytes) -> None:
        # The public release never captures or exports outbound requests.
        if isinstance(flow, tuple) and len(flow) == 5 and flow[0] == "outbound":
            return
        collector.feed_flow(flow, data)

    def on_packet_copy(flow, seq, payload):
        if debug_recorder is not None:
            debug_recorder.record_packet(flow, seq, payload)

    capture = PassivePacketCapture(
        process_name,
        on_stream_data,
        on_status=lambda text: writer.emit("log", message=text),
        on_flow_closed=collector.close_flow,
        on_packet_copy=on_packet_copy,
        on_ready=on_capture_ready,
        on_stats=emit_stats,
        stop_event=stop_event,
        capture_outbound=False,
    )

    def finalize_debug(reason: str) -> dict[str, object]:
        if debug_recorder is None:
            return {}
        try:
            return debug_recorder.finalize(
                capture_stats=last_net_stats,
                payload_counts=collector.payload_counts,
                framing_stats=collector.framing_diagnostics(),
                reason=reason,
            )
        except Exception as exc:
            writer.emit("log", message=f"[디버그] 진단 ZIP 생성 실패: {type(exc).__name__}")
            return {"error": type(exc).__name__}

    monitor_done = threading.Event()

    def set_terminal(reason: str, **extra: object) -> bool:
        with terminal_lock:
            if terminal.get("reason"):
                return False
            terminal.clear()
            terminal.update({"reason": reason, **extra})
        capture.request_stop()
        return True

    def monitor() -> None:
        while not monitor_done.wait(0.20):
            if not owner_alive(owner_lease):
                if set_terminal("cancelled"):
                    writer.emit("log", message="Exporter 종료 · 캡처 중지 요청")
                return
            command = read_control(control_path)
            if command == "cancel":
                if set_terminal("cancelled"):
                    writer.emit("log", message="사용자가 캡처 중지를 요청했습니다.")
                return

            if not capture_ready_event.is_set():
                continue


            event = lifecycle.observe(observed_snapshot(), time.monotonic())
            emit_stats()
            if event == "login_complete":
                if set_terminal("complete"):
                    if lifecycle.completed_at_deadline:
                        writer.emit("log", message="필수 데이터 수집 완료 · 제한시간 종료 · 백업 검증 및 저장 중…")
                    else:
                        writer.emit("log", message="계정 데이터 12종 수신 완료 · 백업 검증 및 저장 중…")
                return
            if event == "login_incomplete":
                if set_terminal("incomplete", missing=list(lifecycle.missing_types)):
                    writer.emit(
                        "log",
                        message="로그인 캡처 제한시간이 끝났지만 필수 데이터가 모두 수신되지 않았습니다. 캡처를 종료합니다.",
                    )
                return

    monitor_thread = threading.Thread(target=monitor, name="gfl2-capture-state", daemon=True)
    monitor_thread.start()

    capture_error: Exception | None = None
    try:
        capture.run()
    except Exception as exc:
        capture_error = exc
    finally:
        monitor_done.set()
        monitor_thread.join(timeout=1.5)

    if capture_error is not None:
        debug_manifest = finalize_debug("capture_error")
        if debug_manifest:
            writer.emit("debug_bundle", manifest=debug_manifest)
        writer.emit("error", message=f"캡처 실패: {type(capture_error).__name__}")
        return 4

    try:
        collector.flush()
        emit_stats()
    except Exception as exc:
        debug_manifest = finalize_debug("flush_error")
        if debug_manifest:
            writer.emit("debug_bundle", manifest=debug_manifest)
        writer.emit("error", message=f"수집 데이터 정리 실패: {type(exc).__name__}")
        return 5

    reason = str(terminal.get("reason") or "cancelled")
    debug_manifest = finalize_debug(reason)
    if debug_manifest:
        writer.emit("debug_bundle", manifest=debug_manifest)
    if reason == "complete":
        try:
            manifest = collector.export(reason="login_sync", integrated_states=True)
        except Exception as exc:
            writer.emit("error", message=f"CSV/ZIP 저장 실패: {type(exc).__name__}")
            return 6
        if manifest.get("export_reason") == "login_sync_incomplete":
            coverage = manifest.get("account_packet_coverage", {})
            missing = [row["packet_id"] for row in coverage.get("packets", [])
                       if row["capture_state"] != "partial_decoder_observed"]
            writer.emit("incomplete", missing=missing, manifest=manifest, debug_manifest=debug_manifest)
            writer.emit("helper_exit", reason="incomplete")
            return 8
        writer.emit("export", manifest=manifest)
        writer.emit(
            "ready_to_close",
            bundle=str(manifest.get("bundle") or ""),
            debug_bundle=str(debug_manifest.get("bundle") or ""),
            counts=manifest.get("counts") or {},
            optional_state={key: manifest.get(key, {}).get("status", "unavailable")
                            for key in ("panel_state", "equipment_state")},
        )

        # The normal GUI shows the completion message first, then writes the
        # terminate command. Keep the elevated helper alive long enough to
        # close GF2 even when the GUI itself has no rights to do so.
        deadline = time.monotonic() + 300.0
        while time.monotonic() < deadline:
            if not owner_alive(owner_lease):
                writer.emit("helper_exit", reason="complete_without_game_termination")
                return 0
            command = read_control(control_path)
            if command == "terminate" and owner_alive(owner_lease):
                terminate_target_processes(process_name, writer)
                writer.emit("helper_exit", reason="complete")
                return 0
            if command in {"leave_game", "cancel"}:
                writer.emit("helper_exit", reason="complete_without_game_termination")
                return 0
            time.sleep(0.10)
        writer.emit("helper_exit", reason="completion_ack_timeout")
        return 0

    if reason == "incomplete":
        try:
            manifest = collector.export(
                reason="login_sync_incomplete",
                bundle_name="gfl2_user_csv_partial.zip",
                bundle_dir=output_dir,
            )
        except Exception as exc:
            writer.emit("error", message=f"부분 백업 저장 실패: {type(exc).__name__}")
            return 7
        coverage = manifest.get("account_packet_coverage", {})
        missing = [row["packet_id"] for row in coverage.get("packets", [])
                   if row["capture_state"] != "partial_decoder_observed"]
        writer.emit(
            "incomplete",
            missing=missing,
            manifest=manifest,
            debug_manifest=debug_manifest,
        )
        writer.emit("helper_exit", reason="incomplete")
        return 8

    # Manual cancellation may still leave useful diagnostic data, but it is
    # intentionally not reported as a successful full snapshot.
    if sum(observed_snapshot().values()) > 0:
        try:
            manifest = collector.export(
                reason="cancelled_partial",
                bundle_name="gfl2_user_csv_partial.zip",
                bundle_dir=output_dir,
            )
            writer.emit("cancelled", manifest=manifest, debug_manifest=debug_manifest)
        except Exception as exc:
            writer.emit("error", message=f"취소 시 부분 백업 저장 실패: {type(exc).__name__}")
            return 9
    else:
        writer.emit("cancelled", manifest={}, debug_manifest=debug_manifest)
    writer.emit("helper_exit", reason="cancelled")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="windows-packet-capture")
    parser.add_argument("--process", default="GF2_Exilium", choices=("GF2_Exilium", "GF2_Exilium.exe"))
    parser.add_argument("--output", required=True)
    parser.add_argument("--bundle-dir", required=True)
    parser.add_argument("--event-file", required=True)
    parser.add_argument("--control-file", required=True)
    parser.add_argument("--reference-index", type=Path, default=None)
    parser.add_argument("--reference-sha256", default=None)
    parser.add_argument("--owner-lease", type=Path, default=None)
    parser.add_argument("--save-debug", action="store_true", default=False, help="save metadata-only diagnostics; never packet bodies")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.reference_index is not None or args.reference_sha256 is not None:
        from .reference_data import baseline, load_index, select_process_snapshot
        try:
            snapshot = load_index(args.reference_index) if args.reference_index is not None else baseline()
            if args.reference_sha256 is not None and snapshot.sha256 != args.reference_sha256:
                raise ValueError("reference selection changed")
            select_process_snapshot(snapshot)
        except (OSError, ValueError):
            IpcEventWriter(Path(args.event_file)).emit("error", message="참조 데이터 검증 실패 · 캡처 시작을 다시 시도하세요.")
            return 11
    return run_helper(
        str(args.process or "GF2_Exilium"),
        Path(args.output),
        Path(args.bundle_dir),
        Path(args.event_file),
        Path(args.control_file),
        save_debug=args.save_debug,
        full_packet_csv=False,
        owner_lease=args.owner_lease,
    )


if __name__ == "__main__":
    raise SystemExit(main())
