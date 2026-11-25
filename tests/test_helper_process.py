import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from gfl2_packet_csv.helper_process import HelperProcess, exit_status

@pytest.mark.parametrize('code,expected', [(0,True),(258,False)])
def test_wait_handle(code,expected):
    kernel=Mock(); kernel.WaitForSingleObject.return_value=code
    proc=HelperProcess(123,kernel)
    assert proc.exited() is expected
    kernel.WaitForSingleObject.assert_called_once_with(123,0)
    proc.close(); proc.close()
    kernel.CloseHandle.assert_called_once_with(123)
    with pytest.raises(RuntimeError): proc.exited()

def test_wait_failed_is_not_exit():
    kernel=Mock(); kernel.WaitForSingleObject.return_value=0xffffffff
    with pytest.raises(OSError): HelperProcess(123,kernel).exited()

@pytest.mark.parametrize('terminal,saved,expected', [('cancelled',False,'cancelled'),(None,True,'CSV 저장 완료'),(None,False,'비정상 종료')])
def test_exit_status(terminal,saved,expected):
    assert expected in exit_status(terminal,saved)

# Run the real GUI method bodies with lightweight stand-ins, without requiring Qt.
ROOT=Path(__file__).resolve().parents[1]
def fake_window(path, *, exited=False, now=1, events=None):
    from gfl2_packet_csv.ipc import read_events
    src=ast.parse((ROOT/'src/gfl2_packet_csv/gui.py').read_text())
    cls=next(n for n in src.body if isinstance(n,ast.ClassDef) and n.name=='PacketExporterWindow')
    names={'_poll_helper_events','_finish_helper_session','_release_helper_session','_handle_helper_event'}
    methods=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in names]
    module=ast.Module(body=methods,type_ignores=[])
    from gfl2_packet_csv.account_export_scope import ACCOUNT_LABELS
    env={'ACCOUNT_LABELS':ACCOUNT_LABELS,'time':SimpleNamespace(monotonic=lambda:now),'exit_status':exit_status,'read_events':read_events}
    exec(compile(module,'gui_methods','exec'),env)
    Window=type('Window',(),{name:env[name] for name in names})
    w=Window(); w._control_path=path.with_suffix(".control"); w._event_path=path; w._event_offset=0; w._polling_events=False; w._capture_active=True
    w._helper_process=Mock(); w._helper_process.exited.return_value=exited
    w._terminal_status=None; w._backup_saved=False; w._wait_error_reported=False; w._helper_started_at=0
    w._capture_ready=False; w._cancel_requested=False; w._startup_timeout_reported=False; w._exit_read_errors=0
    w._quit_after_helper_exit=False; w._completion_ack_sent=False
    for name in ('activity_log','status_label','start_button','stop_button','save_debug_checkbox','_ipc_timer','_save_session_log','_cancel_capture'):
        setattr(w,name,Mock())
    if events is not None: w._handle_helper_event=events
    return w

def test_terminal_event_does_not_unlock_live_helper(tmp_path):
    w=fake_window(tmp_path/'missing'); proc=w._helper_process
    w._finish_helper_session('terminal')
    assert w._capture_active and not w.start_button.setEnabled.called
    w._poll_helper_events(); assert w._capture_active
    proc.exited.return_value=True
    w._poll_helper_events(); assert not w._capture_active
    proc.close.assert_called_once(); w.start_button.setEnabled.assert_called_once_with(True)

def test_exit_without_file_unlocks(tmp_path):
    w=fake_window(tmp_path/'missing',exited=True)
    w._poll_helper_events(); assert not w._capture_active
    w._save_session_log.assert_called_once_with('error')

def test_final_events_drained_before_exit(tmp_path):
    p=tmp_path/'events'; p.write_text('{"type":"cancelled"}\n')
    w=fake_window(p,exited=True)
    w._poll_helper_events(); assert not w._capture_active and w._terminal_status=='캡처 취소됨'
    assert not p.exists()
    w._save_session_log.assert_called_once_with('cancelled')

def test_startup_timeout_once_no_unlock(tmp_path):
    w=fake_window(tmp_path/'missing',now=31)
    w._poll_helper_events(); w._poll_helper_events()
    w._cancel_capture.assert_called_once(); assert w._capture_active

def test_completed_event_after_exit_no_terminate_request(tmp_path):
    p=tmp_path/'events'; p.write_text('{"type":"ready_to_close"}\n')
    w=fake_window(p,exited=True); w._poll_helper_events()
    assert w._backup_saved and not w._capture_active
    assert 'CSV 저장 완료' in w.status_label.setText.call_args.args[0]

def test_cancel_completion_race_does_not_request_termination(tmp_path):
    w=fake_window(tmp_path/'missing'); w._helper_exited=False; w._cancel_requested=True
    w._handle_helper_event({'type':'ready_to_close'})
    assert w._backup_saved and w._completion_ack_sent
    assert '게임 종료하지 않음' in w.status_label.setText.call_args.args[0]

def test_rollback_script_checks_errors_before_relaunch():
    from gfl2_packet_csv.update_engine import _UPDATER_PS
    rollback=_UPDATER_PS.split('$rollbackErrors = @()',1)[1].split('exit 1',1)[0]
    assert '$rollbackErrors.Count -eq 0' in rollback
    assert 'Destination occupied' in rollback and 'Backup missing' in rollback
    assert 'Get-Process -Id $AppPid' in rollback
    assert 'Remove-Item -LiteralPath $dest -Recurse -Force -ErrorAction Stop' in rollback
