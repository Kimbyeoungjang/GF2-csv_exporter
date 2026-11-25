import importlib.util
import io
import struct
import zipfile
from pathlib import Path
import pytest
from gfl2_packet_csv import bundle_import, backup_preflight


def load_builder():
    path = Path(__file__).resolve().parents[1] / 'packaging' / 'build_offline_import.py'
    spec = importlib.util.spec_from_file_location('offline_builder', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_corrupt_deflate_is_normal_invalid_backup(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('manifest.json', '{}')
        for name in bundle_import.CORE_HEADERS:
            z.writestr(name, '')
    data = bytearray(buf.getvalue())
    with zipfile.ZipFile(buf) as z:
        off = z.getinfo('manifest.json').header_offset
    n, extra = struct.unpack_from('<HH', data, off + 26)
    data[off+30+n+extra] = 255  # invalid DEFLATE block type
    p = tmp_path / 'bad.zip'
    p.write_bytes(data)
    with pytest.raises(ValueError, match='invalid account bundle structure'):
        bundle_import.read_bundle(p)
    report = backup_preflight.check_backup(p)
    assert report['bundle'] == 'invalid' and backup_preflight.exit_code(report) == 8


def test_offline_generator_uses_separate_namespace(tmp_path):
    builder = load_builder()
    target = tmp_path / 'offline'
    assert builder.generate(target) == 57
    package = target / 'src' / 'gf2_account_import'
    for name in ('capture','gui','helper','collector','update_engine','cli','diagnostics'):
        assert not (package / f'{name}.py').exists()
    assert 'dependencies = []' in (target/'pyproject.toml').read_text()
    assert 'gf2_account_import.static_tables' in (package/'panel_references.py').read_text()
    assert (target/'LICENSE').is_file()
    with pytest.raises(FileExistsError):
        builder.generate(target)


def test_offline_generator_rejects_unreviewed_module(tmp_path):
    import shutil
    builder = load_builder()
    source = tmp_path / 'source'
    shutil.copytree(builder.SOURCE, source, ignore=shutil.ignore_patterns('__pycache__'))
    p = source/'account_import_session.py'
    p.write_text(p.read_text()+'\nfrom . import capture\n')
    with pytest.raises(ValueError, match='Unreviewed local module'):
        builder.closure(source)
