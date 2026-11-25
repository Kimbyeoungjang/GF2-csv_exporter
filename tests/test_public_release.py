import importlib.util
import io
import json
from pathlib import Path
import zipfile

import pytest

from gfl2_packet_csv import diagnostics, wire_probe
from gfl2_packet_csv.activity_log import safe_activity_text
from gfl2_packet_csv.account_export_scope import ACCOUNT_LABELS
from gfl2_packet_csv.capture import PassivePacketCapture, CAPTURE_FILTER
from gfl2_packet_csv.helper import terminate_target_processes

ROOT = Path(__file__).resolve().parents[1]


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'packaging' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_log_preserves_known_events_but_rejects_arbitrary_text():
    valid = ['[시작] CSV Exporter v1.6.0 · 데이터 백업 시작',
             '[참조 데이터] 최신 · 0123456789ab',
             '[저장] CSV ZIP 백업 생성 완료', '소녀전선2 연결 확인 · TCP 2개']
    valid += [f'[수집] {label} 데이터 수신 · {i}/12종' for i, label in enumerate(ACCOUNT_LABELS.values(), 1)]
    valid += ['[정리] 인형 67건, 무기 194건, 파츠 1,234건, 공용키 32건, 리몰딩 300건, 아이템 1,001건']
    private = ['[저장] fictional-player-name', '[오류] opaque-private-value',
               '[연결] 2001:db8::1', '[정리] arbitrary-account-value',
               '[종료] 게임 프로세스 종료 중 일부 오류: 4321',
               '[수집] invented-name 데이터 수신 · 1/12종',
               '[저장] CSV ZIP 백업 생성 완료 extra-data']
    cleaned = safe_activity_text('\n'.join(valid + private))
    assert all(x in cleaned.splitlines() for x in valid)
    assert all(x not in cleaned for x in ('fictional-player-name','opaque-private-value','2001:db8','arbitrary-account-value','4321','invented-name','extra-data'))


def test_public_capture_is_inbound_only():
    assert CAPTURE_FILTER == 'inbound and tcp and tcp.PayloadLength > 0'
    with pytest.raises(ValueError):
        PassivePacketCapture('GF2_Exilium', lambda *a: None, capture_outbound=True)
    with pytest.raises(ValueError):
        PassivePacketCapture('not-the-game', lambda *a: None)
    with pytest.raises(ValueError):
        terminate_target_processes('not-the-game', None)


def test_layout_iterator_matches_value_parser():
    data = b'\x08\x96\x01\x12\x03abc\x1d1234\x21abcdefgh'
    parsed = wire_probe.parse_fields(data)
    assert list(wire_probe.iter_field_layout(data)) == [(n,w,len(v) if w == 2 else None) for n,w,v in parsed]
    for parser in (wire_probe.parse_fields, lambda x: list(wire_probe.iter_field_layout(x))):
        with pytest.raises(wire_probe.WireDecodeError):
            parser(b'\x12\x04a')


def test_diagnostics_shape_and_flow_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, 'MAX_TRACKED_FLOWS', 1)
    monkeypatch.setattr(diagnostics, 'MAX_SHAPE_ROWS', 1)
    d = diagnostics.SanitizedDebugRecorder(tmp_path)
    d.record_packet(('synthetic',1), 100, b'x')
    d.record_packet(('synthetic',2), 200, b'x')
    d.record_payload(11101, b'\x08\x01')
    d.record_payload(11101, b'\x08\x02')
    d.finalize(reason='test')
    with zipfile.ZipFile(tmp_path / 'gfl2_packet_diagnostics.zip') as z:
        manifest = json.loads(z.read('debug_manifest.json'))
        packets = [json.loads(x) for x in z.read('matched_tcp_index.jsonl').splitlines()]
        payloads = [json.loads(x) for x in z.read('game_payload_index.jsonl').splitlines()]
    assert manifest['flow_count_capped'] and manifest['shape_rows_analyzed'] == 1
    assert packets[1]['flow_id'] == 0 and payloads[1]['wire_shape']['status'] == 'shape_budget_exhausted'
    assert manifest['assembled_game_payloads'] == 2


@pytest.mark.parametrize('name', ['_internal/logs/startup.log','_internal/gfl2_packet_debug_raw.zip',
                                 '_internal/backup.csv','_internal/.env','THIRD_PARTY/research_capture.zip',
                                 '_internal/unknown.zip','_internal/equipment_user.json'])
def test_binary_packaging_rejects_private_artifacts(name):
    with pytest.raises(ValueError):
        load_tool('package_release').validate_member(name, notices=name.startswith('THIRD_PARTY/'))


def test_public_source_scanner_fails_without_echoing_secret():
    tool = load_tool('public_source')
    fake = 'gh' + 'p_' + 'a' * 36
    with pytest.raises(ValueError) as err:
        tool.scan_text('src/example.py', fake)
    assert fake not in str(err.value)
    tool.scan_text('src/example.py', 'https://github.com/example/project')


def test_source_packager_excludes_runtime_artifacts(tmp_path):
    tool = load_tool('public_source')
    entries = tool.collect(ROOT)
    assert '.gitignore' in entries and not any(name.endswith('.whl') or name.endswith('.log') for name in entries)
    assert len([name for name in entries if name.endswith('.csv')]) == 113
    assert not any('debug_raw.py' in name for name in entries)
    out = tmp_path / 'public.zip'
    tool.build(out, ROOT)
    with zipfile.ZipFile(out) as z:
        assert z.testzip() is None
        assert set(z.namelist()) == set(entries)


def test_binary_packager_uses_reviewed_resources(tmp_path):
    import shutil
    tool = load_tool('package_release')
    dist = tmp_path / 'dist'
    dist.mkdir()
    for name in tool.EXES:
        (dist / name).write_bytes(b'MZsynthetic')
    src = ROOT / 'src/gfl2_packet_csv'
    for group in ('static_tables', 'static_achievements', 'reference_seed'):
        shutil.copytree(src / group, dist / '_internal/gfl2_packet_csv' / group,
                        ignore=shutil.ignore_patterns('__pycache__', '*.py'))
    notices = tmp_path / 'notices'
    notices.mkdir()
    (notices / 'NOTICE.txt').write_text('Synthetic test notice')
    (dist / 'gfl2_user_csv_bundle.zip').write_bytes(b'synthetic-private-data')
    tool.package(dist, notices, tmp_path / 'output')
    with zipfile.ZipFile(tmp_path / 'output' / tool.NAME) as z:
        assert z.testzip() is None
        assert not any('gfl2_user_csv_bundle' in n for n in z.namelist())
    target = dist / '_internal/gfl2_packet_csv/static_tables/item_reference.csv'
    target.write_bytes(target.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='Frozen reference content'):
        tool.package(dist, notices, tmp_path / 'other')
