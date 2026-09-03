from __future__ import annotations

from pathlib import Path

from gfl2_packet_csv.capture import TcpStreamReassembler
from gfl2_packet_csv.collector import PacketCollector
from gfl2_packet_csv.framing import GameMessageFramer


def _frame(msg_id: int, payload_type: int, data: bytes) -> bytes:
    body = payload_type.to_bytes(2, "little") + len(data).to_bytes(2, "little") + data
    return msg_id.to_bytes(3, "little") + len(body).to_bytes(2, "little") + body


def test_framer_resyncs_when_capture_starts_mid_stream() -> None:
    framer = GameMessageFramer()
    valid = _frame(0x1234, 11101, b"hello")
    out = framer.feed(b"\xff\x81MIDSTREAM-JUNK" + valid)
    assert [(p.payload_type, p.data) for p in out] == [(11101, b"hello")]
    assert framer.resync_dropped > 0


def test_framer_does_not_resync_inside_fragmented_frame_after_sync() -> None:
    framer = GameMessageFramer()
    # Establish a known-good boundary first, matching the real login flow.
    first = _frame(1, 10002, b"ready")
    assert [p.payload_type for p in framer.feed(first)] == [10002]

    # The large frame body intentionally contains bytes that themselves form
    # a complete plausible GF2 frame.  While the outer frame is incomplete,
    # an incremental framer must wait instead of jumping into those body bytes.
    false_inner = _frame(0x3344, 23102, b"looks-like-a-frame")
    data = b"A" * 24 + false_inner + b"B" * 9000
    outer = _frame(15, 11101, data)
    split = 5 + 4 + 24 + len(false_inner) + 17

    assert framer.feed(outer[:split]) == []
    out = framer.feed(outer[split:])
    assert [(p.payload_type, p.data) for p in out] == [(11101, data)]
    assert framer.resync_dropped == 0


def test_per_flow_framers_do_not_mix_interleaved_tcp_streams(tmp_path: Path) -> None:
    collector = PacketCollector(tmp_path)
    a = _frame(101, 60001, b"A" * 12)
    b = _frame(202, 60002, b"B" * 13)
    collector.feed_flow(("10.0.0.2", 1, "1.1.1.1", 2), a[:7])
    collector.feed_flow(("10.0.0.2", 3, "2.2.2.2", 4), b[:9])
    collector.feed_flow(("10.0.0.2", 1, "1.1.1.1", 2), a[7:])
    collector.feed_flow(("10.0.0.2", 3, "2.2.2.2", 4), b[9:])
    assert collector.payload_counts[60001] == 1
    assert collector.payload_counts[60002] == 1


def test_tcp_reassembler_handles_gap_and_duplicate() -> None:
    stream = TcpStreamReassembler()
    assert stream.feed(1000, b"abc") == b"abc"
    assert stream.feed(1006, b"ghi") == b""
    assert stream.feed(1003, b"def") == b"defghi"
    assert stream.feed(1003, b"def") == b""


def test_packet_backend_is_server_to_client_copy_only() -> None:
    root = Path(__file__).resolve().parents[1]
    capture = (root / "src/gfl2_packet_csv/capture.py").read_text(encoding="utf-8")
    helper = (root / "src/gfl2_packet_csv/helper.py").read_text(encoding="utf-8")
    project = (root / "pyproject.toml").read_text(encoding="utf-8")

    assert '"inbound and tcp and tcp.PayloadLength > 0"' in capture
    assert "Flag.SNIFF | Flag.RECV_ONLY" in capture
    assert ".send(" not in capture
    assert "WinDivertSend" not in capture
    assert "outbound and tcp" not in capture
    assert "mitmproxy" not in project.casefold()
    assert "pydivert>=3.1.3" in project
    # Game termination is a separate post-export process action, never a
    # packet/socket mutation in the capture backend.
    assert "terminate_target_processes" in helper
    assert "collector.export(reason=\"login_sync\")" in helper


def test_passive_run_observes_copy_without_reinjecting(monkeypatch) -> None:
    import sys
    import threading
    import types
    import gfl2_packet_csv.capture as capture_mod

    stop = threading.Event()
    delivered: list[tuple[object, bytes]] = []
    opened: dict[str, object] = {}

    class FakeTcp:
        seq_num = 100

    class FakePacket:
        dst_addr = "10.0.0.2"
        dst_port = 50000
        src_addr = "203.0.113.10"
        src_port = 443
        payload = b"abcdef"
        tcp = FakeTcp()

    class FakeDivert:
        def __init__(self, packet_filter, *, flags):
            opened["filter"] = packet_filter
            opened["flags"] = flags
            self.done = False
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def recv(self, timeout=None):
            if self.done:
                raise TimeoutError()
            self.done = True
            return FakePacket()

    class FakeFlag:
        SNIFF = 1
        RECV_ONLY = 4

    fake_pydivert = types.ModuleType("pydivert")
    fake_pydivert.WinDivert = FakeDivert
    fake_consts = types.ModuleType("pydivert.consts")
    fake_consts.Flag = FakeFlag
    monkeypatch.setitem(sys.modules, "pydivert", fake_pydivert)
    monkeypatch.setitem(sys.modules, "pydivert.consts", fake_consts)
    monkeypatch.setattr(capture_mod.os, "name", "nt")
    monkeypatch.setattr(capture_mod, "is_windows_admin", lambda: True)

    class FakeTracker:
        def maybe_refresh(self, *, force=False):
            return capture_mod.TargetConnectionSnapshot(
                pids=(123,),
                process_names=("GF2_Exilium.exe",),
                inbound_keys=frozenset({("10.0.0.2", 50000, "203.0.113.10", 443)}),
            )
        def matches_inbound(self, _packet):
            return True

    def on_data(flow, data):
        delivered.append((flow, data))
        stop.set()

    cap = capture_mod.PassivePacketCapture("GF2_Exilium", on_data, stop_event=stop)
    cap.tracker = FakeTracker()
    cap.run()
    assert delivered == [(("10.0.0.2", 50000, "203.0.113.10", 443), b"abcdef")]
    assert opened["flags"] == (FakeFlag.SNIFF | FakeFlag.RECV_ONLY)
    assert str(opened["filter"]).startswith("inbound and tcp")


def test_stop_does_not_double_close_handle() -> None:
    root = Path(__file__).resolve().parents[1]
    capture = (root / "src/gfl2_packet_csv/capture.py").read_text(encoding="utf-8")
    request_stop = capture.split("def request_stop", 1)[1].split("def _recv_packet", 1)[0]
    assert ".close(" not in request_stop
    assert "WinDivertShutdown" in request_stop
    assert "stop_event.set()" in request_stop


def test_closed_flow_is_pruned_without_touching_target_connection(monkeypatch) -> None:
    import gfl2_packet_csv.capture as capture_mod

    flow = ("10.0.0.2", 50000, "203.0.113.10", 443)
    closed = []
    cap = capture_mod.PassivePacketCapture("GF2_Exilium", lambda _flow, _data: None, on_flow_closed=closed.append)
    cap.streams[flow] = capture_mod.TcpStreamReassembler()
    cap._flow_last_seen[flow] = 10.0
    cap._flow_close_grace = 5.0
    monkeypatch.setattr(capture_mod.time, "monotonic", lambda: 16.0)
    cap._prune_closed_flows(capture_mod.TargetConnectionSnapshot(inbound_keys=frozenset()))
    assert flow not in cap.streams
    assert closed == [flow]
    assert cap.stats.active_flows == 0


def test_gui_uses_dedicated_elevated_helper_without_restarting_main() -> None:
    root = Path(__file__).resolve().parents[1]
    gui = (root / "src/gfl2_packet_csv/gui.py").read_text(encoding="utf-8")
    main_spec = (root / "packaging/packet.spec").read_text(encoding="utf-8")
    helper_spec = (root / "packaging/helper.spec").read_text(encoding="utf-8")
    build = (root / "build_exe.bat").read_text(encoding="utf-8")

    assert "windows-packet-capture.exe" in gui
    assert 'shell_execute(None, "open"' in gui
    assert '"runas"' not in gui
    assert "--elevated-capture" not in gui
    assert "_restart_elevated" not in gui
    assert "QTabWidget" not in gui
    assert "_build_decode_panel" not in gui
    assert "자동 디코드 · CSV" not in gui
    assert "관찰 모드" not in gui
    assert "uac_admin=False" in main_spec
    assert "uac_admin=True" in helper_spec
    assert "windows-packet-capture.exe" in build


def test_completion_is_finite_and_requests_game_shutdown_after_notice() -> None:
    root = Path(__file__).resolve().parents[1]
    gui = (root / "src/gfl2_packet_csv/gui.py").read_text(encoding="utf-8")
    helper = (root / "src/gfl2_packet_csv/helper.py").read_text(encoding="utf-8")
    assert "필요한 데이터를 모두 수집해 CSV 백업을 저장했습니다" in gui
    assert 'write_control(self._control_path, "terminate")' in gui
    assert '"ready_to_close"' in helper
    assert 'command == "terminate"' in helper
    assert "terminate_target_processes(process_name, writer)" in helper
    assert "watch_export" not in helper


def test_warning_is_restored_and_not_persisted() -> None:
    root = Path(__file__).resolve().parents[1]
    gui = (root / "src/gfl2_packet_csv/gui.py").read_text(encoding="utf-8")
    assert "이 프로그램은 패킷을 캡처해 CSV 데이터를 생성합니다" in gui
    assert "사용으로 발생하는 모든 문제와 책임은 사용자에게 있습니다" in gui
    assert "동의하고 실행" in gui
    assert "QSettings" not in gui


def test_cli_does_not_force_main_gui_elevation() -> None:
    root = Path(__file__).resolve().parents[1]
    cli = (root / "src/gfl2_packet_csv/cli.py").read_text(encoding="utf-8")
    spec = (root / "packaging/packet.spec").read_text(encoding="utf-8")
    assert "uac_admin=True" not in spec
    assert "not is_windows_admin()" in cli
    assert '"runas"' not in cli


def test_game_shutdown_targets_only_exact_configured_process(monkeypatch) -> None:
    import sys
    import types
    import gfl2_packet_csv.helper as helper_mod

    calls: list[tuple[str, int]] = []

    class FakeProc:
        def __init__(self, pid: int, name: str):
            self.pid = pid
            self.info = {"pid": pid, "name": name}
        def terminate(self):
            calls.append(("terminate", self.pid))
        def kill(self):
            calls.append(("kill", self.pid))

    target = FakeProc(10, "GF2_Exilium.exe")
    other = FakeProc(11, "GF2_Exilium_CrashReporter.exe")
    fake_psutil = types.ModuleType("psutil")
    fake_psutil.NoSuchProcess = type("NoSuchProcess", (Exception,), {})
    fake_psutil.AccessDenied = type("AccessDenied", (Exception,), {})
    fake_psutil.process_iter = lambda _attrs: [target, other]
    fake_psutil.wait_procs = lambda procs, timeout: (list(procs), [])
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    class Writer:
        def __init__(self):
            self.events = []
        def emit(self, event_type, **payload):
            self.events.append((event_type, payload))

    writer = Writer()
    result = helper_mod.terminate_target_processes("GF2_Exilium", writer)
    assert calls == [("terminate", 10)]
    assert result["matched_pids"] == [10]
    assert writer.events[-1][0] == "target_terminated"


def test_launcher_accepts_pydivert_313_blocking_recv_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    launcher = (root / "start_packet_csv_exporter.bat").read_text(encoding="utf-8")
    capture = (root / "src/gfl2_packet_csv/capture.py").read_text(encoding="utf-8")

    # PyDivert 3.1.3 exposes blocking recv() without a timeout keyword.
    # Runtime validation must only require a usable WinDivert class; the
    # capture backend itself handles both recv API variants.
    assert "'timeout' in inspect.signature(D.recv).parameters" not in launcher
    assert 'assert D is not None' in launcher
    assert 'Runtime is incomplete. Repairing dependencies...' in launcher
    assert 'self._recv_accepts_timeout = False' in capture
    assert 'return handle.recv()' in capture


def test_pyinstaller_specs_resolve_entries_from_specpath() -> None:
    root = Path(__file__).resolve().parents[1]
    helper_spec = (root / "packaging/helper.spec").read_text(encoding="utf-8")
    packet_spec = (root / "packaging/packet.spec").read_text(encoding="utf-8")
    launcher = (root / "start_packet_csv_exporter.bat").read_text(encoding="utf-8")
    build = (root / "build_exe.bat").read_text(encoding="utf-8")

    for spec, entry in ((helper_spec, "helper_entry.py"), (packet_spec, "packet_entry.py")):
        assert "SPECPATH" in spec
        assert 'SPEC_DIR = os.path.abspath(SPECPATH)' in spec
        assert f'ENTRY_SCRIPT = os.path.join(SPEC_DIR, "{entry}")' in spec
        assert 'SRC_DIR = os.path.join(PROJECT_ROOT, "src")' in spec
        assert f'["packaging/{entry}"]' not in spec

    assert 'set "APP_ROOT=%~dp0"' in launcher
    assert '"%APP_ROOT%packaging\\helper.spec"' in launcher
    assert 'set "APP_ROOT=%~dp0"' in build
    assert '"%APP_ROOT%packaging\\helper.spec"' in build
    assert '"%APP_ROOT%packaging\\packet.spec"' in build


def test_capture_readiness_is_emitted_only_after_handle_open() -> None:
    root = Path(__file__).resolve().parents[1]
    capture = (root / "src/gfl2_packet_csv/capture.py").read_text(encoding="utf-8")
    helper = (root / "src/gfl2_packet_csv/helper.py").read_text(encoding="utf-8")
    gui = (root / "src/gfl2_packet_csv/gui.py").read_text(encoding="utf-8")
    handle_pos = capture.index("self._active_handle = handle")
    ready_pos = capture.index("self.on_ready(self.stats.as_dict())")
    assert handle_pos < ready_pos
    assert 'writer.emit("capture_ready"' in helper
    assert 'if not capture_ready_event.is_set()' in helper
    assert 'if kind == "capture_ready"' in gui
    assert "이제 소녀전선2에 로그인하세요" in gui


def test_sanitized_debug_metadata_is_collected_before_reassembly_and_decode() -> None:
    root = Path(__file__).resolve().parents[1]
    capture = (root / "src/gfl2_packet_csv/capture.py").read_text(encoding="utf-8")
    collector = (root / "src/gfl2_packet_csv/collector.py").read_text(encoding="utf-8")
    helper = (root / "src/gfl2_packet_csv/helper.py").read_text(encoding="utf-8")
    debug = (root / "src/gfl2_packet_csv/debug_raw.py").read_text(encoding="utf-8")
    assert capture.index("self.on_packet_copy(flow, seq_num, raw)") < capture.index("stream.feed(seq_num, raw)")
    assert collector.index("self.on_raw_payload(int(payload_type), raw)") < collector.index("self._decode_dolls(data)")
    assert "gfl2_packet_debug_raw.zip" in debug
    assert "matched_tcp_index.jsonl" in debug
    assert "game_payload_index.jsonl" in debug
    assert "SanitizedDebugRecorder(output_dir)" in helper
    assert "no_tcp_payload_bodies" in debug
    assert "no_game_payload_bodies" in debug


def test_debug_capture_remains_target_matched_only() -> None:
    root = Path(__file__).resolve().parents[1]
    capture = (root / "src/gfl2_packet_csv/capture.py").read_text(encoding="utf-8")
    debug = (root / "src/gfl2_packet_csv/debug_raw.py").read_text(encoding="utf-8")
    assert "if self.tracker.matches_inbound(packet):" in capture
    assert "self._promote_pending_for_flow(flow)" in capture
    assert "self._deliver_matched_packet(flow, seq_num, payload)" in capture
    assert "elif self.tracker.snapshot.process_found:" in capture
    assert "self._defer_unmatched_packet(flow, seq_num, payload)" in capture
    assert "no_ip_addresses_or_ports" in debug
    assert "metadata-only" in debug


def test_deferred_first_response_is_promoted_when_flow_becomes_target(monkeypatch) -> None:
    import gfl2_packet_csv.capture as capture_mod

    flow = ("10.0.0.2", 50000, "203.0.113.10", 443)
    delivered = []
    raw_copies = []
    cap = capture_mod.PassivePacketCapture(
        "GF2_Exilium",
        lambda f, data: delivered.append((f, data)),
        on_packet_copy=lambda f, seq, data: raw_copies.append((f, seq, data)),
    )
    monkeypatch.setattr(capture_mod.time, "monotonic", lambda: 100.0)
    cap._defer_unmatched_packet(flow, 100, b"first")
    assert raw_copies == []  # unconfirmed traffic is not persisted
    assert cap.stats.packets_deferred == 1

    snapshot = capture_mod.TargetConnectionSnapshot(inbound_keys=frozenset({flow}))
    cap._promote_pending(snapshot)
    assert raw_copies == [(flow, 100, b"first")]
    assert delivered == [(flow, b"first")]
    assert cap.stats.packets_promoted == 1


def test_sanitized_debug_archive_contains_no_packet_or_payload_bodies(tmp_path: Path) -> None:
    import json
    import zipfile

    from gfl2_packet_csv.debug_raw import SanitizedDebugRecorder

    flow = ("10.0.0.2", 50000, "203.0.113.10", 443)
    tcp_secret = b"Authorization: Bearer super-secret-token"
    payload_secret = b"protobuf-body-with-login-token"
    rec = SanitizedDebugRecorder(tmp_path)
    rec.record_packet(flow, 1234, tcp_secret)
    rec.record_payload(11101, payload_secret)
    manifest = rec.finalize(
        capture_stats={"packets_seen": 2, "packets_matched": 1},
        payload_counts={11101: 1},
        framing_stats={"frames_decoded": 1},
        reason="test",
    )

    bundle = tmp_path / "gfl2_packet_debug_raw.zip"
    assert bundle.is_file()
    assert manifest["schema_version"] == 2
    assert manifest["privacy_mode"] == "metadata_only"
    assert manifest["matched_tcp_packets"] == 1
    assert manifest["assembled_payload_type_counts"] == {"11101": 1}
    assert not (tmp_path / "debug_raw").exists()

    with zipfile.ZipFile(bundle) as zf:
        names = set(zf.namelist())
        assert names == {
            "PRIVACY.txt",
            "debug_manifest.json",
            "game_payload_index.jsonl",
            "matched_tcp_index.jsonl",
        }
        assert not any(name.endswith(".bin") for name in names)
        blob = b"\n".join(zf.read(name) for name in sorted(names))
        assert tcp_secret not in blob
        assert payload_secret not in blob
        assert b"10.0.0.2" not in blob
        assert b"203.0.113.10" not in blob
        assert b"50000" not in blob
        packet = json.loads(zf.read("matched_tcp_index.jsonl"))
        payload = json.loads(zf.read("game_payload_index.jsonl"))
        assert set(packet) == {"index", "flow_id", "relative_tcp_seq", "length", "content_tag"}
        assert "wire_shape" in payload
        assert "content_tag" in payload


def test_sanitized_content_tags_are_archive_local(tmp_path: Path) -> None:
    import json
    import zipfile

    from gfl2_packet_csv.debug_raw import SanitizedDebugRecorder

    tags = []
    for index in (1, 2):
        out = tmp_path / str(index)
        rec = SanitizedDebugRecorder(out)
        rec.record_packet(("127.0.0.1", 1, "127.0.0.2", 2), 100, b"same-secret")
        rec.finalize(reason="test")
        with zipfile.ZipFile(out / "gfl2_packet_debug_raw.zip") as zf:
            tags.append(json.loads(zf.read("matched_tcp_index.jsonl"))["content_tag"])
    assert tags[0] != tags[1]


def test_repeated_waiting_status_is_throttled_but_state_changes_are_immediate(monkeypatch) -> None:
    import gfl2_packet_csv.capture as capture_mod

    now = {"value": 0.0}
    monkeypatch.setattr(capture_mod.time, "monotonic", lambda: now["value"])
    messages: list[str] = []
    cap = capture_mod.PassivePacketCapture("GF2_Exilium", lambda _flow, _data: None, on_status=messages.append)

    waiting = capture_mod.TargetConnectionSnapshot()
    cap._emit_tracker_status(waiting, first=True)
    now["value"] = 0.35
    cap._emit_tracker_status(waiting, first=False)
    now["value"] = 7.99
    cap._emit_tracker_status(waiting, first=False)
    assert messages == ["소녀전선2 실행 대기 중"]

    now["value"] = 8.0
    cap._emit_tracker_status(waiting, first=False)
    assert messages == ["소녀전선2 실행 대기 중", "소녀전선2 실행 대기 중"]

    now["value"] = 8.1
    running = capture_mod.TargetConnectionSnapshot(pids=(123,), process_names=("GF2_Exilium.exe",))
    cap._emit_tracker_status(running, first=False)
    assert messages[-1] == "소녀전선2 실행 확인 · 서버 연결 대기 중"


def test_gui_is_trimmed_and_bundle_target_is_application_root() -> None:
    root = Path(__file__).resolve().parents[1]
    gui = (root / "src/gfl2_packet_csv/gui.py").read_text(encoding="utf-8")
    helper = (root / "src/gfl2_packet_csv/helper.py").read_text(encoding="utf-8")
    assert "대상 프로세스" not in gui
    assert "저장 폴더" not in gui
    assert "메인 화면은 일반 권한" not in gui
    assert "def _application_root()" in gui
    assert 'return _application_root() / "gfl2_packet_backup"' in gui
    assert 'return _application_root()' in gui
    assert '"--bundle-dir", str(bundle_dir)' in gui
    assert 'parser.add_argument("--bundle-dir", required=True)' in helper


def test_tcp_reassembler_tracks_pending_bytes_without_full_resum() -> None:
    from gfl2_packet_csv.capture import TcpStreamReassembler

    stream = TcpStreamReassembler()
    assert stream.feed(100, b"abc") == b"abc"
    assert stream.pending_bytes == 0
    assert stream.feed(106, b"ghi") == b""
    assert stream.pending_bytes == 3
    assert stream.feed(103, b"def") == b"defghi"
    assert stream.pending_bytes == 0


def test_connected_status_does_not_spam_when_only_tcp_count_changes(monkeypatch) -> None:
    import gfl2_packet_csv.capture as capture_mod

    now = {"value": 1.0}
    monkeypatch.setattr(capture_mod.time, "monotonic", lambda: now["value"])
    messages: list[str] = []
    cap = capture_mod.PassivePacketCapture("GF2_Exilium", lambda _flow, _data: None, on_status=messages.append)
    one = capture_mod.TargetConnectionSnapshot(pids=(1,), inbound_keys=frozenset({("a", 1, "b", 2)}))
    two = capture_mod.TargetConnectionSnapshot(
        pids=(1,),
        inbound_keys=frozenset({("a", 1, "b", 2), ("a", 3, "b", 4)}),
    )
    cap._emit_tracker_status(one, first=True)
    now["value"] = 1.5
    cap._emit_tracker_status(two, first=False)
    assert messages == ["소녀전선2 연결 확인 · TCP 1개"]


def test_launcher_version_matches_pyproject() -> None:
    import re
    import tomllib

    root = Path(__file__).resolve().parents[1]
    with (root / "pyproject.toml").open("rb") as fp:
        project_version = tomllib.load(fp)["project"]["version"]
    launcher = (root / "start_packet_csv_exporter.bat").read_text(encoding="utf-8")
    match = re.search(r'set "APP_VERSION=([^"]+)"', launcher)
    assert match is not None
    assert match.group(1) == project_version


def test_sanitized_debug_start_removes_legacy_raw_artifacts(tmp_path: Path) -> None:
    from gfl2_packet_csv.debug_raw import SanitizedDebugRecorder

    legacy_dir = tmp_path / "debug_raw" / "matched_tcp"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "flow_001.bin").write_bytes(b"old-sensitive-packet")
    legacy_zip = tmp_path / "gfl2_packet_debug_raw.zip"
    legacy_zip.write_bytes(b"old-sensitive-zip")

    SanitizedDebugRecorder(tmp_path)

    assert not (tmp_path / "debug_raw").exists()
    assert not legacy_zip.exists()


def test_package_version_matches_pyproject() -> None:
    import ast
    import tomllib

    root = Path(__file__).resolve().parents[1]
    with (root / "pyproject.toml").open("rb") as fp:
        project_version = tomllib.load(fp)["project"]["version"]
    module = ast.parse((root / "src" / "gfl2_packet_csv" / "__init__.py").read_text(encoding="utf-8"))
    package_version = None
    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__version__":
                    package_version = ast.literal_eval(node.value)
    assert package_version == project_version
