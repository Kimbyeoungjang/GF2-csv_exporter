import json
import zipfile
from pathlib import Path
from gfl2_packet_csv import __version__, update_check, backup_preflight
from gfl2_packet_csv.diagnostics import SanitizedDebugRecorder
from gfl2_packet_csv.activity_log import safe_activity_text
from gfl2_packet_csv.account_export_scope import ACCOUNT_LABELS, DEFAULT_EXPORT_TYPES
from test_helper_process import fake_window

ROOT = Path(__file__).resolve().parents[1]

def test_release_version_consistency():
    import tomllib
    assert __version__ == '1.6.0'
    assert tomllib.loads((ROOT/'pyproject.toml').read_text())['project']['version'] == __version__
    assert f'APP_VERSION={__version__}' in (ROOT/'start_packet_csv_exporter.bat').read_text()
    assert update_check.is_newer_version('v1.6.0', '1.6.0-RC130')
    assert update_check.is_newer_version('v1.6.0', '1.5.0')
    assert not update_check.is_newer_version('v1.6.0', '1.6.0')
    assert not update_check.is_newer_version('v1.6.0-RC130', '1.6.0')


def test_all_twelve_labels_logged(tmp_path):
    assert set(ACCOUNT_LABELS) == DEFAULT_EXPORT_TYPES and len(set(ACCOUNT_LABELS.values())) == 12
    w = fake_window(tmp_path/'events')
    for i, label in enumerate(ACCOUNT_LABELS.values(), 1):
        w._handle_helper_event({'type':'account_section','label':label,'completed':i,'total':12})
    lines = [c.args[0] for c in w.activity_log.append.call_args_list]
    assert len(lines) == 12 and '12/12종' in lines[-1]
    assert all(label in line for label, line in zip(ACCOUNT_LABELS.values(), lines))


def test_debug_metadata_only(tmp_path):
    recorder = SanitizedDebugRecorder(tmp_path)
    value = b'SYNTHETIC_PRIVATE_TOKEN_abc123'
    body = bytes([10, len(value)]) + value
    recorder.record_packet(('192.0.2.45', 12345, '198.51.100.90', 443), 123456789, body)
    recorder.record_payload(11101, body)
    recorder.finalize(reason='test', capture_stats={'private': 'SYNTHETIC_PRIVATE_TOKEN_abc123'})
    with zipfile.ZipFile(tmp_path/'gfl2_packet_diagnostics.zip') as z:
        assert set(z.namelist()) == {'debug_manifest.json','PRIVACY.txt','matched_tcp_index.jsonl','game_payload_index.jsonl'}
        combined = b'\n'.join(z.read(n) for n in z.namelist())
        for secret in (value,b'192.0.2.45',b'198.51.100.90',b'123456789',b'12345'):
            assert secret not in combined
        r = json.loads(z.read('game_payload_index.jsonl'))
        assert r['wire_shape']['field_counts'] == {'1:2':1}


def test_activity_scrubs_direct_identifiers():
    text = safe_activity_text('[오류] Authorization: Bearer SECRET\n[저장] C:\\Users\\private\\x.zip\n[오류] test@example.com 192.0.2.12 9876543210')
    assert all(s not in text for s in ('SECRET','private','test@example.com','192.0.2.12','9876543210'))
