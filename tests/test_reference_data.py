import hashlib
import importlib.util
import io
import json
from pathlib import Path
import zipfile
from concurrent.futures import ThreadPoolExecutor
import pytest
from gfl2_packet_csv.reference_data import baseline, current, load_index, load_pack, use_snapshot, ReferenceMismatch
from gfl2_packet_csv.doll_growth_catalog import class_caps
from gfl2_packet_csv.collector import PacketCollector
from gfl2_packet_csv.bundle_import import read_bundle
from gfl2_packet_csv.backup_preflight import check_backup

ROOT = Path(__file__).resolve().parents[1]


def publish(source, dest):
    spec = importlib.util.spec_from_file_location('publisher', ROOT/'packaging/publish_reference_pack.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.publish(source, dest)


@pytest.fixture
def changed(tmp_path):
    source = tmp_path/'source'
    for n, b in baseline().data.items():
        p = source/n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b)
    p = source/'static_tables/doll_class_caps.csv'
    p.write_text(p.read_text().replace('5,60', '5,65'), encoding='utf-8')
    dest = tmp_path/'release'
    publish(source, dest)
    return dest/'reference_data.json'


def test_baseline_pack_round_trip(tmp_path):
    entry = publish(ROOT/'src/gfl2_packet_csv', tmp_path)
    assert len(baseline().data) == 113
    assert load_index(tmp_path/'reference_data.json').metadata() == baseline().metadata()
    p = tmp_path/entry['file']
    with p.open('ab') as f: f.write(b'x')
    with pytest.raises(ValueError): load_pack(p, entry['sha256'])


def test_snapshot_cache_isolated(changed):
    snap = load_index(changed)
    assert snap.sha256 != baseline().sha256
    assert class_caps()[5] == 60
    with use_snapshot(snap):
        assert class_caps()[5] == 65
        # Separate thread does not inherit an import session's selection.
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(lambda: class_caps()[5]).result() == 60
    assert class_caps()[5] == 60


def test_export_manifest_and_legacy_compat(tmp_path, changed):
    snap = load_index(changed)
    p = tmp_path/'export'
    with use_snapshot(snap):
        m = PacketCollector(p).export(reason='offline_decode')
        assert m['reference_data'] == snap.metadata()
        assert not read_bundle(m['bundle'], allow_partial=True).complete_capture
    with pytest.raises(ReferenceMismatch): read_bundle(m['bundle'], allow_partial=True)
    assert check_backup(m['bundle'])['bundle'] == 'reference_mismatch'
    # Historic V35 backup without a reference field, preserving all other metadata.
    old = PacketCollector(tmp_path/'old').export(reason='offline_decode')['bundle']
    legacy = tmp_path/'legacy.zip'
    with zipfile.ZipFile(old) as src, zipfile.ZipFile(legacy,'w') as dst:
        for n in src.namelist():
            b = src.read(n)
            if n == 'manifest.json':
                d = json.loads(b); del d['reference_data']; b = json.dumps(d).encode()
            dst.writestr(n,b)
    assert not read_bundle(legacy, allow_partial=True).complete_capture
    with use_snapshot(snap), pytest.raises(ReferenceMismatch): read_bundle(legacy, allow_partial=True)


def test_bad_index_rejected(changed):
    entry = json.loads(changed.read_text())
    entry['file'] = '../outside.zip'
    changed.write_text(json.dumps(entry))
    with pytest.raises(ValueError): load_index(changed)
    assert check_backup('missing', reference_index=changed)['bundle'] == 'reference_unavailable'
