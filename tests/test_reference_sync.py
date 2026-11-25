"""Regression against the actual R13-approved public reference contract."""
import hashlib
import json
from pathlib import Path
import zipfile
import pytest
from gfl2_packet_csv import reference_sync as sync
from gfl2_packet_csv.reference_data import baseline, load_index, use_snapshot
from gfl2_packet_csv.collector import PacketCollector
from gfl2_packet_csv.bundle_import import read_bundle
from gfl2_packet_csv.backup_preflight import check_backup
from gfl2_packet_csv.item_catalog import catalog
from gfl2_packet_csv.counter_rule_catalog import counter_rules, condition_references
from test_reference_data import publish

FIXTURES = Path(__file__).parent / 'fixtures'
BASE = 'https://example.invalid/project/api/v1'


@pytest.fixture
def api():
    seed = sync.SEED_INDEX
    index = seed.read_bytes()
    e = json.loads(index)
    prefix = BASE + '/exporter-reference/'
    files = {
        BASE + '/manifest.json': (FIXTURES / 'manifest.json').read_bytes(),
        prefix + 'approval.json': (FIXTURES / 'approval.json').read_bytes(),
        prefix + 'latest.json': index,
        prefix + e['reference_sha256'] + '/reference_data.json': index,
        prefix + e['reference_sha256'] + '/' + e['file']: (seed.parent / e['file']).read_bytes(),
    }
    requests = []
    def get(url, limit):
        requests.append(url)
        result = files[url]
        assert len(result) <= limit
        return result
    return e, files, requests, get


def configure(root):
    (root / 'reference_api_url.txt').write_text(BASE)


def offline(root):
    (root / 'reference_api_url.txt').write_text('')


def test_online_actual_approved_pack(tmp_path, monkeypatch, api):
    e, files, requests, get = api
    monkeypatch.setattr(sync, 'SEED_INDEX', tmp_path / 'missing')
    configure(tmp_path)
    first = sync.refresh(tmp_path, get=get)
    assert first.status == 'updated' and first.identity == e['reference_sha256']
    assert load_index(first.index).sha256 == first.identity
    assert len(requests) == 5 and requests[-1].endswith('.zip')
    requests.clear()
    second = sync.refresh(tmp_path, get=get)
    assert second.status == 'current' and second.index == first.index
    assert len(requests) == 4 and not any(u.endswith('.zip') for u in requests)
    with use_snapshot(load_index(first.index)):
        assert len(catalog()) == 8415
        assert all(n in catalog() for n in (110096,110097,110098,110099))
    assert len(catalog()) == 8411


def test_shipped_pack_and_no_network(tmp_path, api):
    e, _, requests, get = api
    offline(tmp_path)
    result = sync.refresh(tmp_path, get=get)
    assert result.status == 'local' and result.identity == e['reference_sha256']
    assert result.index.is_file() and 'versions' in result.index.parts
    assert sync.refresh(tmp_path, get=get).status == 'cached'
    assert not requests


@pytest.mark.parametrize('change', ['unapproved', 'identity', 'table', 'dll', 'policy', 'immutable', 'archive', 'network'])
def test_reject_bad_update_keep_valid_pack(tmp_path, monkeypatch, api, change):
    e, files, requests, get = api
    # A real prior snapshot is installed first; network update must not replace
    # it until every check passes, even when the new pack's JSON looks valid.
    seed = sync.SEED_INDEX
    source = tmp_path / 'baseline_source'
    for n, raw in baseline().data.items():
        p = source / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
    dest = tmp_path / 'reference_data'
    dest.mkdir()
    publish(source, dest)
    offline(tmp_path)
    old = sync.refresh(tmp_path, get=get)
    assert old.identity == baseline().sha256
    configure(tmp_path)
    prefix = BASE + '/exporter-reference/'
    def change_json(url, key, value):
        obj = json.loads(files[url]); obj[key] = value; files[url] = json.dumps(obj).encode()
    if change == 'unapproved':
        obj = json.loads(files[BASE+'/manifest.json']); obj['exporter_reference']['status'] = 'previous-or-unverified'; files[BASE+'/manifest.json'] = json.dumps(obj).encode()
    elif change == 'identity': change_json(prefix+'approval.json','reference_sha256','f'*64)
    elif change == 'table': change_json(prefix+'approval.json','source_table_version','different')
    elif change == 'dll': change_json(prefix+'approval.json','static_code_sha256','invalid')
    elif change == 'policy': change_json(prefix+'approval.json','manual_policy_version','unexpected')
    elif change == 'immutable': change_json(prefix+e['reference_sha256']+'/reference_data.json','sha256','f'*64)
    elif change == 'archive':
        u = prefix+e['reference_sha256']+'/'+e['file']; files[u] = b'x' + files[u][1:]
    elif change == 'network':
        def get(url,limit): raise TimeoutError('offline')
    new = sync.refresh(tmp_path,get=get)
    assert new.status == 'fallback' and new.identity == old.identity and new.index == old.index
    assert json.loads((dest/'active.json').read_bytes())['reference_sha256'] == old.identity
    assert load_index(old.index).sha256 == old.identity


def test_two_versions_retained(tmp_path, api):
    e, files, requests, get = api
    source = tmp_path/'baseline_source'
    for n,b in baseline().data.items():
        p = source/n; p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(b)
    dest = tmp_path/'reference_data'; dest.mkdir()
    publish(source,dest)
    offline(tmp_path)
    old = sync.refresh(tmp_path, get=get)
    configure(tmp_path)
    new = sync.refresh(tmp_path, get=get)
    assert new.status == 'updated' and new.identity == e['reference_sha256']
    assert load_index(old.index).sha256 == old.identity
    assert load_index(new.index).sha256 == new.identity


def test_seed_recovery_corrupt_cache(tmp_path, api):
    e, _, _, get = api
    offline(tmp_path)
    old = sync.refresh(tmp_path, get=get)
    pack = old.index.parent / e['file']
    pack.write_bytes(b'invalid')
    recovered = sync.refresh(tmp_path,get=get)
    assert recovered.status == 'fallback'
    assert load_index(recovered.index).sha256 == e['reference_sha256']


def test_no_seed_offline_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(sync,'SEED_INDEX',tmp_path/'absent')
    offline(tmp_path)
    assert sync.refresh(tmp_path).identity == baseline().sha256
    assert sync.refresh(tmp_path).index is None


def test_actual_pack_item_projection_backup(tmp_path):
    offline(tmp_path)
    selection = sync.refresh(tmp_path)
    with use_snapshot(load_index(selection.index)):
        collector = PacketCollector(tmp_path/'account')
        collector.items[110096] = {'item_id':110096, 'quantity':2}
        manifest = collector.export(reason='offline_decode')
        assert manifest['reference_data']['sha256'] == selection.identity
        b = read_bundle(manifest['bundle'], allow_partial=True)
        assert b.rows['items.csv'][0]['item_id'] == '110096'
        assert b.rows['items.csv'][0]['item_reference_status'] == 'catalog_consistent'
    assert check_backup(manifest['bundle'])['bundle'] == 'reference_mismatch'


@pytest.mark.parametrize('url',['http://example.invalid/api/v1','https://evil.invalid@host.invalid/api/v1','https://host.invalid/api/v1?token=secret','https://host.invalid/../api/v1','https://host.invalid/api/v2','https://host.invalid:444/api/v1'])
def test_reject_untrusted_url_shape(url):
    with pytest.raises(ValueError): sync.normalize_base(url)


def test_official_origin_old_api_uses_seed(tmp_path):
    seen = []
    def old_api(url, limit):
        seen.append(url)
        return b'{"schema_version":2,"data_version":"table.1212770"}'
    selected = sync.refresh(tmp_path,get=old_api)
    assert selected.status == 'fallback'
    assert selected.identity == load_index(sync.SEED_INDEX).sha256
    assert seen == ['https://raw.githubusercontent.com/Kimbyeoungjang/GF2Tools-api/main/api/v1/manifest.json']


def test_additive_achievement_rules_and_conditions():
    import csv, io
    from gfl2_packet_csv.reference_data import _snapshot
    data = dict(baseline().data)
    def extend(name, key, new_id):
        body = data[name]
        reader = csv.DictReader(io.StringIO(body.decode('utf-8-sig')))
        rows = list(reader)
        row = dict(rows[0]); row[key] = str(new_id)
        out = io.StringIO(newline=''); writer = csv.DictWriter(out,fieldnames=reader.fieldnames)
        writer.writeheader(); writer.writerows(rows+[row])
        data[name] = out.getvalue().encode('utf-8-sig')
    rule_id = 900000001; condition_id = 900000002
    extend('static_tables/counter_rule_reference.csv','rule_id',rule_id)
    extend('static_achievements/achievement_conditions_ko.csv','condition_id',condition_id)
    with use_snapshot(_snapshot(data)):
        assert rule_id in counter_rules() and len(counter_rules()) == 549
        assert condition_id in condition_references() and len(condition_references()) == 1399


def test_semantically_invalid_item_catalog_refused(tmp_path):
    # Same schema, a valid ZIP digest and file hashes, but a duplicate item ID.
    source = tmp_path/'source'
    for n,b in baseline().data.items():
        p = source/n; p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(b)
    p = source/'static_tables/item_reference.csv'
    with p.open('ab') as f: f.write(p.read_bytes().splitlines(keepends=True)[1])
    dest = tmp_path/'pack'; publish(source,dest)
    with pytest.raises(ValueError,match='unsupported reference catalog'):
        sync._validate(dest/'reference_data.json')
