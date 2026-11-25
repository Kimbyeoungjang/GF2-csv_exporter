from __future__ import annotations

import argparse
import os
import subprocess
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QProgressBar,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .activity_log import save_activity_log
from .account_export_scope import ACCOUNT_LABELS
from .helper_process import HelperProcess, exit_status
from .session_lock import SessionLock, SessionBusy, gui_lock, capture_lock
from .ipc import ipc_root
from .capture import passive_capture_preflight
from .ipc import cleanup_stale_ipc, control_path_for, event_path_for, read_events, write_control
from .reference_sync import refresh as refresh_reference
from .update_check import configured_repository, fetch_latest_release, is_newer_version
from . import __version__
from .update_engine import download_and_stage, launch_staged_update, prepare_recovery

_TARGET_PROCESS = "GF2_Exilium"


class _UpdateSignals(QObject):
    result = Signal(object)
    progress = Signal(int)
    download_finished = Signal(object)
    reference_ready = Signal(object)


def _application_root() -> Path:
    """Directory where normal user-facing output should be placed."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


_STYLE = """
QWidget { background: #0B0E11; color: #EEF1F2; font-size: 13px; }
QMainWindow { background: #0B0E11; }
QFrame#Panel { background: #12171B; border: 1px solid #30383E; border-radius: 10px; }
QFrame#Hero { background: #101519; border: 1px solid #30383E; border-radius: 10px; }
QLabel { background: transparent; }
QLabel#Title { font-size: 23px; font-weight: 700; }
QLabel#SectionTitle { font-size: 15px; font-weight: 700; }
QLabel#Muted { color: #98A2A8; }
QLabel#Version { color: #FFD6B8; background: #35231B; border: 1px solid #8C4B25; border-radius: 7px; padding: 5px 10px; font-weight: 700; }
QCheckBox { background: transparent; spacing: 9px; color: #D3DADE; }
QLabel#Status { color: #C6D0D5; background: #0C1114; border: 1px solid #30383E; border-radius: 7px; padding: 9px; }
QTextEdit { background: #090D10; border: 1px solid #39434A; border-radius: 6px; padding: 7px; selection-background-color: #26383B; }
QTextEdit:focus { border-color: #687982; }
QPushButton { background: #171D21; border: 1px solid #3A444B; border-radius: 7px; padding: 8px 12px; font-weight: 600; }
QPushButton:hover { background: #222B31; }
QPushButton:disabled { color: #5F686D; background: #101519; border-color: #252D32; }
QPushButton#Primary { background: #F27622; border-color: #FF8A3D; color: #171009; }
QPushButton#Danger { background: #351E21; border-color: #6A373D; }
QProgressBar { background: #101519; border: 1px solid #30383E; border-radius: 4px; text-align: center; min-height: 16px; }
QProgressBar::chunk { background: #F27622; }
"""


class PacketExporterWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"GFL2 Packet CSV Exporter v{__version__}")
        self.resize(980, 720)
        self.setMinimumSize(840, 620)

        self._owner_lease: Path | None = None
        self._event_path: Path | None = None
        self._control_path: Path | None = None
        self._event_offset = 0
        self._polling_events = False
        self._capture_active = False
        self._completion_ack_sent = False
        self._missing_account_packet_types: tuple[int, ...] = ()
        self._session_log_saved = False
        self._last_connection_state = None
        self._helper_started_at = 0.0
        self._helper_process: HelperProcess | None = None
        self._terminal_status: str | None = None
        self._backup_saved = False
        self._quit_after_helper_exit = False
        self._termination_requested = False
        self._wait_error_reported = False
        self._capture_ready = False
        self._cancel_requested = False
        self._startup_timeout_reported = False
        self._exit_read_errors = 0
        self._helper_exited = False
        self._update_check_started = False
        self._manual_update_check = False
        self._updating = False
        self._reference_checking = False
        self._closing = False
        self._reference_index = None
        self._reference_identity = None
        self._update_signals = _UpdateSignals(self)
        self._update_signals.result.connect(self._handle_update_result)
        self._update_signals.progress.connect(self._update_download_progress)
        self._update_signals.download_finished.connect(self._download_finished)
        self._update_signals.reference_ready.connect(self._reference_ready)

        cleanup_stale_ipc()
        self.setCentralWidget(self._build_page())
        self._ipc_timer = QTimer(self)
        self._ipc_timer.setInterval(150)
        self._ipc_timer.timeout.connect(self._poll_helper_events)

    def _build_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(18, 16, 18, 18)
        outer.setSpacing(12)

        hero = QFrame()
        hero.setObjectName("Hero")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(16, 14, 16, 14)
        hero_layout.setSpacing(5)
        title = QLabel("GFL2 Packet CSV Exporter")
        title.setObjectName("Title")
        title_row = QHBoxLayout()
        title_row.addWidget(title)
        title_row.addStretch(1)
        version = QLabel(f"v{__version__}")
        version.setObjectName("Version")
        version.setToolTip("CSV Exporter 프로그램 버전")
        title_row.addWidget(version)
        hero_layout.addLayout(title_row)
        desc = QLabel("인형 · 무기 · 지원소대 등 계정 데이터를 CSV로 저장합니다.")
        desc.setObjectName("Muted")
        desc.setWordWrap(True)
        hero_layout.addWidget(desc)
        outer.addWidget(hero)

        capture_panel = QFrame()
        capture_panel.setObjectName("Panel")
        capture_layout = QVBoxLayout(capture_panel)
        capture_layout.setContentsMargins(14, 13, 14, 13)
        capture_layout.setSpacing(10)

        capture_title = QLabel("로그인 데이터 캡처")
        capture_title.setObjectName("SectionTitle")
        capture_layout.addWidget(capture_title)

        self.status_label = QLabel("대기 중 · 캡처를 시작하세요.")
        self.status_label.setObjectName("Status")
        self.status_label.setWordWrap(True)
        capture_layout.addWidget(self.status_label)

        button_row = QHBoxLayout()
        self.start_button = QPushButton("캡처 시작")
        self.start_button.setObjectName("Primary")
        self.start_button.clicked.connect(self._start_capture)
        self.stop_button = QPushButton("중지")
        self.stop_button.setObjectName("Danger")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._cancel_capture)
        diagnose = QPushButton("환경 진단")
        diagnose.clicked.connect(self._diagnose_environment)
        open_button = QPushButton("결과 폴더 열기")
        open_button.clicked.connect(self._open_output)
        button_row.addWidget(self.start_button)
        button_row.addWidget(self.stop_button)
        button_row.addWidget(diagnose)
        button_row.addWidget(open_button)
        button_row.addStretch(1)
        capture_layout.addLayout(button_row)
        self.save_debug_checkbox = QCheckBox("디버그 진단 저장")
        self.save_debug_checkbox.setChecked(False)
        self.save_debug_checkbox.setToolTip("패킷 개수와 필드 형태 등 진단 통계만 저장합니다. 패킷 원문, IP, 계정 인증 정보 및 채팅 내용은 저장하지 않습니다.")
        capture_layout.addWidget(self.save_debug_checkbox)
        outer.addWidget(capture_panel)
        updater_row = QHBoxLayout()
        updater_row.addWidget(QLabel(f"버전 {__version__}"))
        self.update_btn = QPushButton("업데이트 확인")
        self.update_btn.clicked.connect(lambda: self.start_update_check(manual=True))
        updater_row.addWidget(self.update_btn)
        self.update_progress = QProgressBar()
        self.update_progress.setRange(0, 100)
        self.update_progress.hide()
        updater_row.addWidget(self.update_progress, 1)
        updater_row.addStretch(1)
        outer.addLayout(updater_row)

        log_panel = QFrame()
        log_panel.setObjectName("Panel")
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(14, 12, 14, 14)
        log_header = QHBoxLayout()
        log_title = QLabel("작업 로그")
        log_title.setObjectName("SectionTitle")
        log_header.addWidget(log_title)
        log_header.addStretch(1)
        clear_log = QPushButton("지우기")
        clear_log.clicked.connect(lambda: self.activity_log.clear())
        log_header.addWidget(clear_log)
        log_layout.addLayout(log_header)
        self.activity_log = QTextEdit()
        self.activity_log.setReadOnly(True)
        self.activity_log.setPlaceholderText("진행 상황이 여기에 표시됩니다.")
        log_layout.addWidget(self.activity_log, 1)
        outer.addWidget(log_panel, 1)
        return page

    def start_update_check(self, *, manual: bool = False) -> None:
        """Network work runs in a daemon thread, never on the Qt GUI thread."""
        if self._update_check_started or self._updating or self._reference_checking:
            return
        repository = configured_repository()
        if not repository:
            return
        self._update_check_started = True
        self._manual_update_check = manual
        self.update_btn.setEnabled(False)

        def worker() -> None:
            try:
                info = fetch_latest_release(repository)
            except Exception:
                info = None
            self._update_signals.result.emit(info)

        threading.Thread(target=worker, name="github-update-check", daemon=True).start()

    def _handle_update_result(self, release) -> None:
        self._update_check_started = False
        self.update_btn.setEnabled(not self._reference_checking)
        manual = self._manual_update_check
        self._manual_update_check = False
        if release is None:
            if manual:
                QMessageBox.information(self, "업데이트 확인", "릴리스 정보를 확인하지 못했습니다. 나중에 다시 시도하세요.")
            return
        if not is_newer_version(release.version, __version__):
            if manual:
                QMessageBox.information(self, "업데이트 확인", "현재 최신 버전입니다.")
            return
        if self._capture_active or self._reference_checking:
            if manual:
                QMessageBox.information(self, "업데이트 확인", "캡처가 끝난 후 업데이트할 수 있습니다.")
            return
        dialog = QMessageBox(self)
        dialog.setWindowTitle("업데이트 확인")
        dialog.setText(f"새 버전 {release.version}이 있습니다.")
        dialog.setInformativeText(f"현재 버전: {__version__}\n\n설치할까요? 나중에 설치해도 계속 사용할 수 있습니다.")
        install = None
        if getattr(sys, "frozen", False) and release.asset_url:
            install = dialog.addButton("업데이트", QMessageBox.ButtonRole.AcceptRole)
        page = dialog.addButton("릴리스 페이지", QMessageBox.ButtonRole.ActionRole)
        dialog.addButton("나중에", QMessageBox.ButtonRole.RejectRole)
        dialog.exec()
        if dialog.clickedButton() is page:
            QDesktopServices.openUrl(QUrl(release.page_url))
        elif install is not None and dialog.clickedButton() is install:
            self._download_update(release)

    def _download_update(self, release) -> None:
        if self._capture_active or self._updating or self._reference_checking:
            return
        try:
            with capture_lock():
                pass
        except (SessionBusy, OSError):
            QMessageBox.warning(self, "업데이트", "다른 캡처가 실행 중이거나 상태를 확인할 수 없습니다.")
            return
        self._updating = True
        self.update_btn.setEnabled(False)
        self.start_button.setEnabled(False)
        self.update_progress.setValue(0)
        self.update_progress.show()
        self.status_label.setText(f"버전 {release.version} 다운로드 중…")

        def worker() -> None:
            try:
                staged = download_and_stage(release, _application_root(),
                                            progress=lambda value: self._update_signals.progress.emit(min(95, value)))
                try:
                    staged = prepare_recovery(staged, _application_root())
                except Exception:
                    shutil.rmtree(staged.work_dir, ignore_errors=True)
                    raise
                self._update_signals.progress.emit(100)
            except Exception as exc:
                self._update_signals.download_finished.emit((None, str(exc)))
            else:
                self._update_signals.download_finished.emit((staged, None))

        threading.Thread(target=worker, name="github-release-download", daemon=True).start()

    def _update_download_progress(self, value: int) -> None:
        self.update_progress.setValue(max(0, min(100, value)))

    def _download_finished(self, result) -> None:
        staged, error = result
        if error:
            self._updating = False
            self.update_btn.setEnabled(True)
            self.start_button.setEnabled(True)
            self.update_progress.hide()
            self.status_label.setText("업데이트 실패 · 기존 버전을 계속 사용할 수 있습니다.")
            QMessageBox.warning(self, "업데이트 실패", error)
            return
        try:
            # Recheck after download: another capture may have started meanwhile.
            with capture_lock():
                launch_staged_update(staged, _application_root())
        except Exception as exc:
            self._updating = False
            self.update_btn.setEnabled(True)
            self.start_button.setEnabled(True)
            self.update_progress.hide()
            QMessageBox.warning(self, "업데이트 실패", str(exc))
            return
        self.status_label.setText("다운로드 완료 · 프로그램을 종료하고 업데이트합니다.")
        QApplication.instance().quit()

    def _output_path(self) -> Path:
        return _application_root() / "gfl2_packet_backup"

    def _bundle_path(self) -> Path:
        return _application_root()

    def _open_output(self) -> None:
        path = self._bundle_path().resolve()
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def _helper_executable(self) -> Path:
        name = "windows-packet-capture.exe"
        if getattr(sys, "frozen", False):
            app_dir = Path(sys.executable).resolve().parent
            candidates = [app_dir / name, app_dir / "bin" / name]
        else:
            root = Path(__file__).resolve().parents[2]
            candidates = [root / "bin" / name, root / name]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return candidates[0]

    def _launch_capture_helper(self, process: str, output: Path, bundle_dir: Path) -> bool:
        if os.name != "nt":
            raise RuntimeError("실시간 캡처는 Windows에서만 지원합니다.")
        helper = self._helper_executable()
        if not helper.is_file():
            raise FileNotFoundError(
                f"관리자 캡처 도구를 찾지 못했습니다: {helper}\n"
                "start_packet_csv_exporter.bat을 실행하면 자동으로 준비됩니다."
            )
        assert self._event_path is not None and self._control_path is not None
        args = [
            "--process", process,
            "--output", str(output),
            "--bundle-dir", str(bundle_dir),
            "--event-file", str(self._event_path),
            "--control-file", str(self._control_path),
        ]
        if self._reference_index is not None:
            args.extend(["--reference-index", str(self._reference_index)])
        if self._reference_identity is not None:
            args.extend(["--reference-sha256", self._reference_identity])
        if self._owner_lease is not None:
            args.extend(["--owner-lease", str(self._owner_lease)])
        if self.save_debug_checkbox.isChecked():
            args.append("--save-debug")
        params = subprocess.list2cmdline(args)
        # Retain the exact process handle returned by ShellExecuteExW.
        self._helper_process = HelperProcess.launch(helper, params)
        return self._helper_process is not None

    def _start_capture(self) -> None:
        if self._capture_active or self._updating or self._reference_checking:
            return
        self._reference_checking = True
        self.start_button.setEnabled(False)
        self.update_btn.setEnabled(False)
        self.save_debug_checkbox.setEnabled(False)
        self.status_label.setText("참조 데이터 확인 중…")
        root = _application_root()

        def worker():
            try:
                result = refresh_reference(root)
            except Exception:
                result = None
            self._update_signals.reference_ready.emit(result)

        threading.Thread(target=worker, name="reference-sync", daemon=True).start()

    def _reference_ready(self, selection) -> None:
        self._reference_checking = False
        if self._closing:
            return
        self.start_button.setEnabled(True)
        self.update_btn.setEnabled(not self._update_check_started)
        self.save_debug_checkbox.setEnabled(True)
        if selection is None:
            self.status_label.setText("참조 데이터 확인 실패 · 다시 시도하세요.")
            return
        self._reference_index = selection.index
        self._reference_identity = selection.identity
        self._begin_capture(selection)

    def _begin_capture(self, selection) -> None:
        process = _TARGET_PROCESS
        output = self._output_path().resolve()
        bundle_dir = self._bundle_path().resolve()
        output.mkdir(parents=True, exist_ok=True)
        bundle_dir.mkdir(parents=True, exist_ok=True)

        token = uuid.uuid4().hex
        self._event_path = event_path_for(token)
        self._control_path = control_path_for(token)
        self._event_offset = 0
        self._terminal_status = None
        self._backup_saved = False
        self._quit_after_helper_exit = False
        self._termination_requested = False
        self._wait_error_reported = False
        self._capture_ready = False
        self._cancel_requested = False
        self._startup_timeout_reported = False
        self._exit_read_errors = 0
        self._helper_exited = False
        self._completion_ack_sent = False
        self._missing_account_packet_types: tuple[int, ...] = ()
        self._session_log_saved = False
        self._last_connection_state = None
        self.activity_log.clear()
        for path in (self._event_path, self._control_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

        self.activity_log.append(f"[시작] CSV Exporter v{__version__} · 데이터 백업 시작")
        label = {"updated": "갱신 완료", "current": "최신", "local": "로컬", "cached": "캐시",
                 "baseline": "내장", "fallback": "갱신 불가 · 기존 데이터 사용"}.get(selection.status, "확인 완료")
        self.activity_log.append(f"[참조 데이터] {label} · {selection.identity[:12]}")
        self.activity_log.append("[설정] 디버그 데이터: " + ("생성" if self.save_debug_checkbox.isChecked() else "생성 안 함"))
        self.activity_log.append("[준비] 관리자 캡처 도구 실행 및 게임 연결 확인 중")
        self.status_label.setText("캡처 도구 시작 중…")
        try:
            launched = self._launch_capture_helper(process, output, bundle_dir)
        except Exception as exc:
            self.status_label.setText("캡처 도구 시작 실패")
            self.activity_log.append(f"[오류] {type(exc).__name__}")
            QMessageBox.critical(self, "캡처 시작 실패", f"캡처 도구 실행 실패 ({type(exc).__name__})")
            return
        if not launched:
            self.status_label.setText("관리자 권한 요청이 취소되었습니다.")
            self.activity_log.append("[캡처] Windows 관리자 권한 요청이 취소되었습니다.")
            return

        self._capture_active = True
        self._helper_started_at = time.monotonic()
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.save_debug_checkbox.setEnabled(False)
        self._ipc_timer.start()

    def _cancel_capture(self) -> None:
        if not self._capture_active or self._control_path is None:
            return
        self.stop_button.setEnabled(False)
        self.status_label.setText("캡처 중지 요청 중…")
        self.activity_log.append("[캡처] 중지 요청")
        try:
            write_control(self._control_path, "cancel")
            self._cancel_requested = True
        except OSError as exc:
            self.stop_button.setEnabled(True)
            self.status_label.setText("중지 요청 실패 · 다시 시도하세요.")
            self.activity_log.append(f"[IPC 오류] {type(exc).__name__}")

    def _diagnose_environment(self) -> None:
        process = _TARGET_PROCESS
        report = passive_capture_preflight(process)
        helper = self._helper_executable()
        lines = [
            f"Windows: {'정상' if report.get('windows') else '지원 안 함'}",
            f"캡처 도구: {'정상' if helper.is_file() else '없음'}",
            f"PyDivert: {'정상' if report.get('pydivert') else '없음'} {report.get('pydivert_version') or ''}",
            f"소녀전선2: {'실행 중' if report.get('process_found') else '실행 대기'}",
            f"TCP 연결: {int(report.get('connection_count') or 0)}개",
            "캡처 방식: inbound / SNIFF / RECV_ONLY",
        ]
        if report.get("filter_error"):
            lines.append(f"필터/구성요소: {report.get('filter_error')}")
        if report.get("process_error"):
            lines.append(f"프로세스 탐지: {report.get('process_error')}")
        text = "\n".join(lines)
        self.activity_log.append("[환경 진단]\n" + text)
        QMessageBox.information(self, "캡처 환경 진단", text)

    def _poll_helper_events(self) -> None:
        path = self._event_path
        if path is None or self._polling_events or not self._capture_active:
            return
        self._polling_events = True
        try:
            # Query before reading: if signalled, the writer has already exited,
            # so its final complete events can be drained before reporting failure.
            exited = False
            if self._helper_process is not None:
                try:
                    exited = self._helper_process.exited()
                except OSError:
                    if not self._wait_error_reported:
                        self._wait_error_reported = True
                        self.activity_log.append('[IPC 오류] 캡처 도구 종료 확인 실패')
            self._helper_exited = exited
            if path.exists():
                try:
                    self._event_offset, events = read_events(path, self._event_offset)
                except (OSError, UnicodeError) as exc:
                    self.activity_log.append(f"[IPC 오류] {type(exc).__name__}")
                    if not exited:
                        return
                    self._exit_read_errors += 1
                    if self._exit_read_errors < 3:
                        return
                    events = []  # dead writer, no indefinite lock on unreadable IPC
                for event in events:
                    self._handle_helper_event(event)
            elif not exited and time.monotonic() - self._helper_started_at > 12.0:
                self.status_label.setText('캡처 도구 응답 대기 중…')
            if (not exited and not self._capture_ready and not self._backup_saved
                    and self._terminal_status is None and not self._cancel_requested
                    and not self._startup_timeout_reported
                    and time.monotonic() - self._helper_started_at >= 30.0):
                self._startup_timeout_reported = True
                self.activity_log.append('[캡처] 시작 응답 제한시간 초과')
                self._cancel_capture()
            if exited:
                status = exit_status(self._terminal_status, self._backup_saved)
                if self._terminal_status is None:
                    self.activity_log.append('[오류] ' + status)
                    self._save_session_log('error')
                self._release_helper_session(status)
        finally:
            self._polling_events = False

    def _handle_helper_event(self, event: dict) -> None:
        kind = str(event.get("type") or "")
        if kind == "helper_started":
            self.status_label.setText("캡처 준비 중…")
            self.activity_log.append("[준비] 관리자 캡처 도구 실행 확인 · 네트워크 수집 준비 중")
            return
        if kind == "account_section":
            label = str(event.get("label") or "데이터")
            completed = int(event.get("completed") or 0)
            total = int(event.get("total") or 12)
            self.activity_log.append(f"[수집] {label} 데이터 수신 · {completed}/{total}종")
            return
        if kind == "capture_ready":
            self._capture_ready = True
            self.status_label.setText("캡처 준비 완료 · 이제 소녀전선2에 로그인하세요.")
            self.activity_log.append("[캡처] 준비 완료 · 게임에 로그인하세요.")
            return
        if kind == "log":
            message = str(event.get("message") or "")
            if message:
                self.activity_log.append(message)
            return
        if kind == "stats":
            seen = int(event.get("sync_seen") or 0)
            required = int(event.get("sync_required") or 7)
            process_found = bool(event.get("process_found"))
            connections = int(event.get("connection_count") or 0)
            matched = int(event.get("packets_matched") or 0)
            connection_state = connections > 0
            if connection_state != self._last_connection_state:
                self._last_connection_state = connection_state
                self.activity_log.append("[연결] 게임 TCP 연결 " + ("감지 · 계정 동기화 수신 중" if connection_state else "대기 중"))
            prefix = "추가 데이터 확인 중" if required > 0 and seen >= required else "수집 중"
            optional_total = int(event.get("sync_optional_total") or 0)
            optional_seen = int(event.get("sync_optional_seen") or 0)
            progress = f"{seen}/{required}"
            if "sync_account_seen" in event:
                progress = f"{int(event['sync_account_seen'])}/12"
            elif optional_total:
                progress = f"{seen + optional_seen}/{required + optional_total}"
            self.status_label.setText(
                f"{prefix} · {progress} · 게임 {'연결됨' if connections > 0 else ('실행 중' if process_found else '대기')} · "
                f"TCP {connections} · 패킷 {matched:,}"
            )
            return
        if kind == "debug_bundle":
            manifest = event.get("manifest") or {}
            if isinstance(manifest, dict):
                bundle = str(manifest.get("bundle") or "")
                if bundle:
                    self.activity_log.append("[디버그] 진단 ZIP 저장 완료")
            return
        if kind == "export":
            self._backup_saved = True
            manifest = event.get("manifest") or {}
            if isinstance(manifest, dict):
                bundle = str(manifest.get("bundle") or "")
                counts = manifest.get("counts") or {}
                csv_files = [name for name, info in (manifest.get("datasets") or {}).items()
                             if name.endswith(".csv") and isinstance(info, dict) and info.get("present")]
                if isinstance(counts, dict):
                    summary = ", ".join(f"{label} {counts.get(key, 0):,}건" for key, label in
                                        (("dolls", "인형"), ("weapons", "무기"),
                                         ("attachments", "파츠"), ("common_keys", "공용키"),
                                         ("remoldings", "리몰딩"), ("items", "아이템")))
                    self.activity_log.append("[정리] " + summary)
                if isinstance(csv_files, list):
                    self.activity_log.append(f"[저장] CSV {len(csv_files)}종 및 manifest 생성")
                extension_status = manifest.get("account_extension_status") or {}
                if isinstance(extension_status, dict) and extension_status:
                    if extension_status.get("wardrobe_11627") == "missing":
                        self.activity_log.append("[정리] 의상 목록 미수신 · 현재 착장 정보는 별도 보존")
                coverage = manifest.get("account_packet_coverage") or {}
                if isinstance(coverage, dict):
                    packets = coverage.get("packets") or []
                    decoded = sum(r.get("capture_state") == "partial_decoder_observed" for r in packets)
                    self.activity_log.append(f"[정리] 계정 데이터 해독 {decoded}/12종")
                    missing = coverage.get("missing_packet_types") or []
                    self._missing_account_packet_types = tuple(
                        int(t) for t in missing if type(t) is int and t > 0
                    )
                    if self._missing_account_packet_types:
                        self.activity_log.append(
                            f"[확인] 추가 계정 데이터 {len(self._missing_account_packet_types)}종 미수신"
                        )
                support_audit = manifest.get("support_data_audit") or {}
                if isinstance(support_audit, dict):
                    if support_audit.get("chip_reference_check") == "unmatched":
                        self.activity_log.append("[확인] 지원소대 장착 소자 참조 검증 실패")
                if manifest.get("account_extension_export_error"):
                    self.activity_log.append("[정리] 선택 데이터 변환 실패 · 기본 백업 유지")
                for key, label in (("panel_state", "능력치 상태"), ("equipment_state", "장비 상태")):
                    state = manifest.get(key)
                    if isinstance(state, dict):
                        status = state.get("status")
                        self.activity_log.append(f"[저장] {label} CSV" if status == "written"
                                                 else f"[확인] {label} CSV 저장 실패 · 기본 ZIP 유지")
                self.activity_log.append("[저장] CSV ZIP 백업 생성 완료")
            return
        if kind == "ready_to_close":
            self._backup_saved = True
            if self._completion_ack_sent:
                return
            self._completion_ack_sent = True
            if self._helper_exited:
                return  # no live helper to acknowledge
            if self._cancel_requested:
                # A completion event can race a user cancellation. The helper
                # accepts the already published cancel command in its ack loop.
                self.status_label.setText('CSV 저장 완료 · 게임 종료하지 않음')
                return
            bundle = str(event.get("bundle") or "")
            self.status_label.setText("수집 및 저장 완료")
            self.stop_button.setEnabled(False)
            if self._missing_account_packet_types:
                message = (
                    "기본 계정 데이터는 저장됐지만 추가 계정 데이터 일부가 수신되지 않았습니다.\n"
                    f"미수신 패킷: {len(self._missing_account_packet_types)}종\n"
                    "백업 상태는 manifest.json에서 확인할 수 있습니다.\n\n"
                    "CSV Exporter와 소녀전선2를 종료합니다."
                )
            else:
                message = (
                    "계정 데이터 수집 및 CSV 백업이 완료됐습니다.\n\n"
                    "CSV Exporter와 소녀전선2를 종료합니다."
                )
            optional = event.get("optional_state")
            unavailable = [label for key, label in (("panel_state", "능력치 상태"), ("equipment_state", "장비 상태"))
                           if isinstance(optional, dict) and key in optional and optional[key] != "written"]
            if unavailable:
                message += "\n\n추가 CSV 저장 실패: " + ", ".join(unavailable) + "\n기본 ZIP은 저장됐습니다."
            if bundle:
                message += f"\n\n저장 파일: {bundle}"
            if self._missing_account_packet_types or unavailable:
                QMessageBox.warning(self, "백업 일부 데이터 확인", message)
            else:
                QMessageBox.information(self, "캡처 완료", message)
            if self._control_path is not None:
                self.activity_log.append("[종료] 소녀전선2를 종료합니다.")
                try:
                    write_control(self._control_path, "terminate")
                    self._termination_requested = True
                except OSError as exc:
                    self.activity_log.append(f"[IPC 오류] {type(exc).__name__}")
                    self.status_label.setText("CSV 저장 완료 · 게임 종료 요청 실패")
                    QMessageBox.warning(self, "종료 요청 실패", "백업은 저장됐습니다. 게임을 직접 종료하세요.")
            return
        if kind == "target_terminated":
            errors = event.get("errors") or []
            if errors:
                self.activity_log.append("[종료] 게임 프로세스 종료 중 일부 오류: " + "; ".join(map(str, errors)))
            else:
                self.activity_log.append("[종료] 소녀전선2 종료 완료")
            self.status_label.setText("완료 · 종료 중…")
            self._save_session_log("complete")
            self._quit_after_helper_exit = True
            self._finish_helper_session("완료 · 종료 중…")
            return
        if kind == "incomplete":
            missing = event.get("missing") or []
            debug_manifest = event.get("debug_manifest") or {}
            debug_bundle = str(debug_manifest.get("bundle") or "") if isinstance(debug_manifest, dict) else ""
            missing_labels = [ACCOUNT_LABELS.get(t, str(t)) for t in missing]
            self.activity_log.append(f"[미완료] 미수신 또는 해독 실패: {', '.join(missing_labels)}")
            if debug_bundle:
                self.activity_log.append("[디버그] 진단 ZIP 저장 완료")
            self._save_session_log("incomplete")
            self._finish_helper_session("계정 데이터 미완료 · 부분 백업을 확인하세요.")
            message = (
                "로그인 제한시간 내에 필요한 데이터를 모두 얻지 못했습니다.\n"
                "소녀전선2는 종료하지 않았습니다.\n\n"
                f"미수신 또는 해독 실패 타입: {', '.join(missing_labels) if missing else '확인 필요'}"
            )
            if debug_bundle:
                message += f"\n\n진단 ZIP: {debug_bundle}"
            QMessageBox.warning(self, "캡처 미완료", message)
            return
        if kind == "cancelled":
            self.activity_log.append("[캡처] 사용자가 캡처를 취소했습니다.")
            self._save_session_log("cancelled")
            self._finish_helper_session("캡처 취소됨")
            return
        if kind == "error":
            message = str(event.get("message") or "알 수 없는 캡처 오류")
            self.activity_log.append("[캡처 오류] " + message)
            self._save_session_log("error")
            self._finish_helper_session("캡처 오류")
            QMessageBox.critical(self, "캡처 오류", message)
            return
        if kind == "helper_exit":
            if self._terminal_status is not None:
                return
            reason = str(event.get("reason") or "")
            if reason == "complete_without_game_termination":
                self.activity_log.append("[종료] 데이터 저장 완료 · 게임 종료하지 않음")
                self._save_session_log("complete")
                self._finish_helper_session("데이터 저장 완료")
            elif reason == "complete" and self._capture_active:
                # The elevated helper may exit without an earlier
                # target_terminated event reaching the GUI (IPC truncation).
                # Preserve the completed CSV and the local activity log;
                # do not pretend that Windows game termination was verified.
                self.activity_log.append("[종료] CSV 저장 완료 · 게임 종료 결과 확인 필요")
                self._save_session_log("complete")
                self._finish_helper_session("CSV 저장 완료 · 게임 종료 확인 필요")
            elif reason == "completion_ack_timeout" and self._capture_active:
                self.activity_log.append("[종료] 완료 응답 시간 초과 · CSV 백업은 저장됨 · 게임 종료 미확인")
                self._save_session_log("error")
                self._finish_helper_session("CSV 저장 완료 · 게임 종료 미확인")
            elif reason not in {"complete"} and self._capture_active:
                self.activity_log.append("[종료] 캡처 도구 종료")
                self._save_session_log("error")
                self._finish_helper_session(f"캡처 도구 종료 · {reason or '종료'}")

    def _save_session_log(self, reason: str) -> None:
        if self._session_log_saved:
            return
        try:
            # Complete backups live beside the executable; incomplete and
            # cancelled partial ZIPs live under gfl2_packet_backup.
            folder = self._output_path() if reason in ("incomplete", "cancelled", "error", "closed") else self._bundle_path()
            path = save_activity_log(folder,
                                     self.activity_log.toPlainText(), reason=reason)
        except (OSError, ValueError) as exc:
            self.activity_log.append(f"[저장] 작업 로그 저장 실패: {type(exc).__name__}")
            return
        self._session_log_saved = True
        self.activity_log.append(f"[저장] 작업 로그: {path.name}")

    def _finish_helper_session(self, status: str) -> None:
        # A terminal event may precede process exit; keep start/update locked.
        self._terminal_status = status
        self.stop_button.setEnabled(False)
        self.status_label.setText(status)

    def _release_helper_session(self, status: str) -> None:
        if self._helper_process is not None:
            self._helper_process.close()
            self._helper_process = None
        # The helper is confirmed exited before this method is called.
        # Remove the local IPC files rather than retaining session metadata.
        for path in (self._event_path, self._control_path):
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        self._event_path = self._control_path = None
        self._capture_active = False
        self._ipc_timer.stop()
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.save_debug_checkbox.setEnabled(True)
        self.status_label.setText(status)
        if self._quit_after_helper_exit:
            self.start_button.setEnabled(False)
            QTimer.singleShot(250, QApplication.instance().quit)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._closing = True
        if self._capture_active and self._control_path is not None and not self._termination_requested:
            try:
                write_control(self._control_path, "cancel")
            except OSError:
                pass
        if self._capture_active and not self._session_log_saved:
            self.activity_log.append("[종료] 프로그램 창 닫기")
            self._save_session_log("closed")
        if self._helper_process is not None:
            self._helper_process.close()
            self._helper_process = None
        super().closeEvent(event)


def _show_responsibility_dialog() -> bool:
    dialog = QMessageBox()
    dialog.setWindowTitle("사용 전 확인")
    dialog.setIcon(QMessageBox.Icon.Warning)
    dialog.setText("이 프로그램은 패킷을 캡처해 CSV 데이터를 생성합니다.")
    dialog.setInformativeText(
        "사용으로 발생하는 모든 문제와 책임은 사용자에게 있습니다.\n\n"
        "동의하면 ‘동의하고 실행’을, 동의하지 않으면 ‘종료’를 누르세요."
    )
    agree = dialog.addButton("동의하고 실행", QMessageBox.ButtonRole.AcceptRole)
    dialog.addButton("종료", QMessageBox.ButtonRole.RejectRole)
    dialog.exec()
    return dialog.clickedButton() is agree


def _build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(add_help=False)


def main(argv: list[str] | None = None) -> int:
    _build_parser().parse_known_args(argv)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setStyleSheet(_STYLE)
    try:
        with gui_lock():
            if not _show_responsibility_dialog():
                return 0
            lease = ipc_root() / f"{uuid.uuid4().hex}.owner.lock"
            try:
                with SessionLock(lease):
                    window = PacketExporterWindow()
                    window._owner_lease = lease
                    window.show()
                    # Startup must never wait for GitHub.
                    QTimer.singleShot(700, window.start_update_check)
                    return int(app.exec())
            finally:
                # Unique path is never reused, so deleting it cannot split a lock.
                try:
                    lease.unlink(missing_ok=True)
                except OSError:
                    pass
    except SessionBusy:
        QMessageBox.warning(None, "Exporter", "이미 Exporter가 실행 중입니다.")
        return 1
    except OSError:
        QMessageBox.warning(None, "Exporter", "실행 상태를 확인할 수 없습니다.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
