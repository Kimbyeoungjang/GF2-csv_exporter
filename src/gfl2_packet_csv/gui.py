from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
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
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .capture import passive_capture_preflight
from .ipc import cleanup_stale_ipc, control_path_for, event_path_for, write_control
from .update_check import configured_repository, fetch_latest_release, is_newer_version
from . import __version__

_TARGET_PROCESS = "GF2_Exilium"


class _UpdateSignals(QObject):
    result = Signal(object)


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
QLabel#Title { font-size: 23px; font-weight: 700; }
QLabel#SectionTitle { font-size: 15px; font-weight: 700; }
QLabel#Muted { color: #98A2A8; }
QLabel#Status { color: #C6D0D5; background: #0C1114; border: 1px solid #30383E; border-radius: 7px; padding: 9px; }
QTextEdit { background: #090D10; border: 1px solid #39434A; border-radius: 6px; padding: 7px; selection-background-color: #26383B; }
QTextEdit:focus { border-color: #687982; }
QPushButton { background: #171D21; border: 1px solid #3A444B; border-radius: 7px; padding: 8px 12px; font-weight: 600; }
QPushButton:hover { background: #222B31; }
QPushButton:disabled { color: #5F686D; background: #101519; border-color: #252D32; }
QPushButton#Primary { background: #F27622; border-color: #FF8A3D; color: #171009; }
QPushButton#Danger { background: #351E21; border-color: #6A373D; }
"""


class PacketExporterWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GFL2 Packet CSV Exporter")
        self.resize(980, 720)
        self.setMinimumSize(840, 620)

        self._event_path: Path | None = None
        self._control_path: Path | None = None
        self._event_offset = 0
        self._capture_active = False
        self._completion_ack_sent = False
        self._helper_started_at = 0.0
        self._update_check_started = False
        self._update_signals = _UpdateSignals(self)
        self._update_signals.result.connect(self._handle_update_result)

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
        hero_layout.addWidget(title)
        desc = QLabel("로그인 중 수신되는 계정 데이터를 캡처해 CSV 백업으로 저장합니다.")
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
        outer.addWidget(capture_panel)

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

    def start_update_check(self) -> None:
        """Check GitHub Releases in the background; never block normal startup."""
        if self._update_check_started:
            return
        repository = configured_repository()
        if not repository:
            return
        self._update_check_started = True

        def worker() -> None:
            release = fetch_latest_release(repository)
            self._update_signals.result.emit(release)

        threading.Thread(target=worker, name="github-update-check", daemon=True).start()

    def _handle_update_result(self, release) -> None:
        if release is None or not is_newer_version(release.version, __version__):
            return
        dialog = QMessageBox(self)
        dialog.setWindowTitle("업데이트 확인")
        dialog.setIcon(QMessageBox.Icon.Information)
        dialog.setText(f"새 버전 {release.version}이 있습니다.")
        dialog.setInformativeText(
            f"현재 버전: {__version__}\n\n"
            "다운로드 페이지를 열까요? 지금 업데이트하지 않아도 프로그램은 그대로 사용할 수 있습니다."
        )
        open_button = dialog.addButton("다운로드 페이지 열기", QMessageBox.ButtonRole.AcceptRole)
        dialog.addButton("나중에", QMessageBox.ButtonRole.RejectRole)
        dialog.exec()
        if dialog.clickedButton() is open_button:
            QDesktopServices.openUrl(QUrl(release.page_url))

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
        params = subprocess.list2cmdline(args)
        # The helper EXE itself carries requireAdministrator. Using ShellExecute
        # with the normal 'open' verb lets Windows display UAC for that helper;
        # this GUI is neither elevated nor restarted.
        shell_execute = ctypes.windll.shell32.ShellExecuteW
        shell_execute.restype = ctypes.c_void_p
        result = int(shell_execute(None, "open", str(helper), params, str(helper.parent), 0) or 0)
        if result <= 32:
            if result == 5:
                return False
            raise OSError(result, "관리자 캡처 도구 실행에 실패했습니다.")
        return True

    def _start_capture(self) -> None:
        if self._capture_active:
            return
        process = _TARGET_PROCESS
        output = self._output_path().resolve()
        bundle_dir = self._bundle_path().resolve()
        output.mkdir(parents=True, exist_ok=True)
        bundle_dir.mkdir(parents=True, exist_ok=True)

        token = uuid.uuid4().hex
        self._event_path = event_path_for(token)
        self._control_path = control_path_for(token)
        self._event_offset = 0
        self._completion_ack_sent = False
        for path in (self._event_path, self._control_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

        self.activity_log.append("[캡처] 관리자 캡처 도구를 시작합니다.")
        self.status_label.setText("캡처 도구 시작 중…")
        try:
            launched = self._launch_capture_helper(process, output, bundle_dir)
        except Exception as exc:
            self.status_label.setText("캡처 도구 시작 실패")
            self.activity_log.append(f"[오류] {type(exc).__name__}: {exc}")
            QMessageBox.critical(self, "캡처 시작 실패", str(exc))
            return
        if not launched:
            self.status_label.setText("관리자 권한 요청이 취소되었습니다.")
            self.activity_log.append("[캡처] Windows 관리자 권한 요청이 취소되었습니다.")
            return

        self._capture_active = True
        self._helper_started_at = time.monotonic()
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self._ipc_timer.start()

    def _cancel_capture(self) -> None:
        if not self._capture_active or self._control_path is None:
            return
        self.stop_button.setEnabled(False)
        self.status_label.setText("캡처 중지 요청 중…")
        self.activity_log.append("[캡처] 중지를 요청했습니다.")
        write_control(self._control_path, "cancel")

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
        if path is None:
            return
        if not path.exists():
            if self._capture_active and time.monotonic() - self._helper_started_at > 12.0:
                self.status_label.setText("캡처 도구 응답 대기 중…")
            return
        try:
            with path.open("r", encoding="utf-8") as fp:
                fp.seek(self._event_offset)
                while True:
                    line = fp.readline()
                    if not line:
                        break
                    self._event_offset = fp.tell()
                    line = line.strip()
                    if not line:
                        continue
                    import json
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event, dict):
                        self._handle_helper_event(event)
        except (OSError, UnicodeError) as exc:
            self.activity_log.append(f"[IPC 오류] {type(exc).__name__}: {exc}")

    def _handle_helper_event(self, event: dict) -> None:
        kind = str(event.get("type") or "")
        if kind == "helper_started":
            self.status_label.setText("캡처 준비 중…")
            return
        if kind == "capture_ready":
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
            self.status_label.setText(
                f"수집 중 · {seen}/{required} · 게임 {'연결됨' if connections > 0 else ('실행 중' if process_found else '대기')} · "
                f"TCP {connections} · 패킷 {matched:,}"
            )
            return
        if kind == "debug_bundle":
            manifest = event.get("manifest") or {}
            if isinstance(manifest, dict):
                bundle = str(manifest.get("bundle") or "")
                if bundle:
                    self.activity_log.append(f"[디버그] 민감정보 제거 진단 ZIP: {bundle}")
            return
        if kind == "export":
            manifest = event.get("manifest") or {}
            if isinstance(manifest, dict):
                bundle = str(manifest.get("bundle") or "")
                self.activity_log.append("[저장] CSV 백업 생성 완료")
                if bundle:
                    self.activity_log.append(f"[저장] {bundle}")
            return
        if kind == "ready_to_close":
            if self._completion_ack_sent:
                return
            self._completion_ack_sent = True
            bundle = str(event.get("bundle") or "")
            self.status_label.setText("수집 및 저장 완료")
            self.stop_button.setEnabled(False)
            message = (
                "필요한 데이터를 모두 수집해 CSV 백업을 저장했습니다.\n\n"
                "CSV Exporter와 소녀전선2를 종료합니다."
            )
            if bundle:
                message += f"\n\n저장 파일: {bundle}"
            QMessageBox.information(self, "캡처 완료", message)
            if self._control_path is not None:
                self.activity_log.append("[종료] 소녀전선2를 종료합니다.")
                write_control(self._control_path, "terminate")
            return
        if kind == "target_terminated":
            errors = event.get("errors") or []
            if errors:
                self.activity_log.append("[종료] 게임 프로세스 종료 중 일부 오류: " + "; ".join(map(str, errors)))
            else:
                self.activity_log.append("[종료] 소녀전선2 종료 완료")
            self.status_label.setText("완료 · 종료 중…")
            QTimer.singleShot(250, QApplication.instance().quit)
            return
        if kind == "incomplete":
            missing = event.get("missing") or []
            debug_manifest = event.get("debug_manifest") or {}
            debug_bundle = str(debug_manifest.get("bundle") or "") if isinstance(debug_manifest, dict) else ""
            self.activity_log.append(f"[미완료] 필수 데이터 일부가 수신되지 않았습니다: {missing}")
            if debug_bundle:
                self.activity_log.append(f"[디버그] 민감정보 제거 진단 ZIP: {debug_bundle}")
            self._finish_helper_session("필수 데이터 미수신 · 진단 자료를 저장했습니다.")
            message = (
                "로그인 제한시간 내에 필요한 데이터를 모두 얻지 못했습니다.\n"
                "소녀전선2는 종료하지 않았습니다.\n\n"
                f"미수신 데이터 타입: {', '.join(map(str, missing)) if missing else '확인 필요'}"
            )
            if debug_bundle:
                message += f"\n\n진단 ZIP: {debug_bundle}"
            QMessageBox.warning(self, "캡처 미완료", message)
            return
        if kind == "cancelled":
            self.activity_log.append("[캡처] 사용자가 캡처를 취소했습니다.")
            self._finish_helper_session("캡처 취소됨")
            return
        if kind == "error":
            message = str(event.get("message") or "알 수 없는 캡처 오류")
            self.activity_log.append("[캡처 오류] " + message)
            self._finish_helper_session("캡처 오류")
            QMessageBox.critical(self, "캡처 오류", message)
            return
        if kind == "helper_exit":
            reason = str(event.get("reason") or "")
            if reason not in {"complete"} and self._capture_active:
                self._finish_helper_session(f"캡처 도구 종료 · {reason or '종료'}")

    def _finish_helper_session(self, status: str) -> None:
        self._capture_active = False
        self._ipc_timer.stop()
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.status_label.setText(status)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._capture_active and self._control_path is not None and not self._completion_ack_sent:
            try:
                write_control(self._control_path, "cancel")
            except OSError:
                pass
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
    if not _show_responsibility_dialog():
        return 0
    window = PacketExporterWindow()
    window.show()
    # Startup must never wait for GitHub. A failed/slow check is intentionally silent.
    QTimer.singleShot(700, window.start_update_check)
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
