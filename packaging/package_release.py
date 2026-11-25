"""Package the already tested Windows build; never builds or executes it."""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path
import stat
import re
import sys
import tempfile
import zipfile

ROOT = 'GFL2-Packet-CSV-Exporter'
NAME = ROOT + '-v1.6.0-win64.zip'
EXES = (ROOT + '.exe', 'windows-packet-capture.exe')


def plain(p: Path):
    s = p.lstat()
    if stat.S_ISLNK(s.st_mode) or getattr(s, 'st_file_attributes', 0) & 0x400:
        raise ValueError(f'Link or reparse point: {p}')
    return s


def files(p: Path):
    s = plain(p)
    if stat.S_ISREG(s.st_mode):
        yield p
    elif stat.S_ISDIR(s.st_mode):
        for child in sorted(p.iterdir()):
            yield from files(child)
    else:
        raise ValueError(f'Not a regular file or directory: {p}')


# Reject runtime/user artifacts even when accidentally copied into a reviewed
# notices or frozen runtime directory. A clean build is still required.
_BLOCKED_PREFIXES = ('gfl2_user_csv', 'gfl2_packet_diagnostics', 'gfl2_packet_debug',
                     'gfl2-exporter-log', 'research_capture', 'debug_diagnostics',
                     'gfl2_packet_backup', '.gfl2_update', '.env')
_BLOCKED_SUFFIXES = {'.log', '.pcap', '.pcapng', '.dmp', '.bin', '.tmp', '.control',
                     '.jsonl', '.pem', '.key', '.pfx', '.p12', '.whl', '.pyc'}


def validate_member(name: str, *, notices: bool = False):
    parts = name.lower().split('/')
    leaf = parts[-1]
    if (any(part.startswith(_BLOCKED_PREFIXES) or part in {'logs', 'reference_data', '__pycache__', '.git'}
            for part in parts) or Path(leaf).suffix in _BLOCKED_SUFFIXES
            or leaf in {'reference_api_url.txt', 'equipment_user.json', 'manifest.json', 'active.json'}
            or leaf.endswith(('.panel-state.csv', '.equipment-state.csv'))):
        raise ValueError(f'Unexpected local/private release member: {name}')
    if leaf.endswith('.csv') and not (not notices and any(
            name.startswith('_internal/gfl2_packet_csv/' + group + '/')
            for group in ('static_tables', 'static_achievements'))):
        raise ValueError(f'Unreviewed CSV release member: {name}')
    if leaf.endswith(('.zip', '.7z', '.rar', '.tar', '.gz')) and not (not notices and (
            name == '_internal/base_library.zip' or
            re.fullmatch(r'_internal/gfl2_packet_csv/reference_seed/gf2-reference-[0-9a-f]{64}\.zip', name))):
        raise ValueError(f'Unreviewed nested archive: {name}')
    if notices and Path(leaf).suffix not in {'', '.txt', '.md', '.rst', '.html', '.htm'}:
        raise ValueError(f'Not a license/notice text file: {name}')


def package(dist: Path, notices: Path, output: Path):
    # Use the same archive validator as the shipping updater.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
    from gfl2_packet_csv.update_engine import _safe_extract, MAX_DOWNLOAD_BYTES, MAX_EXTRACTED_BYTES, MAX_MEMBERS
    source = Path(__file__).resolve().parents[1]
    if not stat.S_ISDIR(plain(dist).st_mode) or not stat.S_ISDIR(plain(notices).st_mode):
        raise ValueError('dist and notices must be directories')
    entries = {}
    for exe in EXES:
        p = dist / exe
        if not stat.S_ISREG(plain(p).st_mode) or p.stat().st_size == 0:
            raise ValueError(f'Missing executable: {p}')
        with p.open('rb') as f:
            if f.read(2) != b'MZ':
                raise ValueError(f'Not a Windows executable: {p}')
        entries[exe] = p
    internal = dist / '_internal'
    if not stat.S_ISDIR(plain(internal).st_mode):
        raise ValueError('Missing _internal directory')
    for p in files(internal):
        name = p.relative_to(dist).as_posix()
        validate_member(name)
        entries[name] = p
    # Frozen static resources must be identical to the reviewed public source;
    # arbitrary CSVs placed inside their directory are not allowed through.
    resource_root = source / 'src/gfl2_packet_csv'
    expected = {}
    for group, patterns in (('static_tables', ('*.csv',)),
                            ('static_achievements', ('*.csv',)),
                            ('reference_seed', ('*.zip', '*.json'))):
        for pattern in patterns:
            for p in (resource_root / group).glob(pattern):
                expected['_internal/gfl2_packet_csv/' + group + '/' + p.name] = p
    actual = {n for n in entries if any(n.startswith('_internal/gfl2_packet_csv/' + group + '/')
              and Path(n).suffix.lower() in {'.csv', '.zip', '.json'}
              for group in ('static_tables', 'static_achievements', 'reference_seed'))}
    if actual != set(expected):
        raise ValueError('Frozen reference resource inventory differs from reviewed source')
    for name, p in expected.items():
        with p.open('rb') as a, entries[name].open('rb') as b:
            if hashlib.file_digest(a, 'sha256').digest() != hashlib.file_digest(b, 'sha256').digest():
                raise ValueError('Frozen reference content differs from reviewed source: ' + name)
    if not any(k.startswith('_internal/') and p.stat().st_size for k,p in entries.items()):
        raise ValueError('Empty _internal directory')
    reviewed = list(files(notices))
    if not reviewed:
        raise ValueError('Provide reviewed third-party license/notice files')
    for p in reviewed:
        name = 'THIRD_PARTY/' + p.relative_to(notices).as_posix()
        validate_member(name, notices=True)
        entries[name] = p
    entries['LICENSE.txt'] = source / 'LICENSE'
    entries['README.md'] = source / 'README.md'
    entries['RELEASE_NOTES.md'] = source / 'RELEASE_NOTES_ko.md'
    if len(entries) > MAX_MEMBERS or sum(p.stat().st_size for p in entries.values()) > MAX_EXTRACTED_BYTES:
        raise ValueError('Updater archive size/member limit exceeded')
    if len({n.casefold() for n in entries}) != len(entries):
        raise ValueError('Case-insensitive duplicate path')
    output.mkdir(parents=True, exist_ok=True)
    target = output / NAME
    checksum = output / (NAME + '.sha256')
    if target.exists() or checksum.exists():
        raise FileExistsError('Output already exists; choose an empty output folder')
    with tempfile.TemporaryDirectory(prefix='.gf2-release-', dir=output) as td:
        work = Path(td)
        archive = work / NAME
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for name,p in sorted(entries.items()):
                z.write(p, ROOT + '/' + name)
        if archive.stat().st_size > MAX_DOWNLOAD_BYTES:
            raise ValueError('Updater download size limit exceeded')
        with zipfile.ZipFile(archive) as z:
            if z.testzip() is not None:
                raise ValueError('ZIP CRC verification failed')
        stage = work / 'stage'
        stage.mkdir()
        _safe_extract(archive, stage, progress=lambda _: None)
        for name,p in entries.items():
            if name in EXES or name.startswith('_internal/'):
                with p.open('rb') as a, (stage / name).open('rb') as b:
                    if hashlib.file_digest(a, 'sha256').digest() != hashlib.file_digest(b, 'sha256').digest():
                        raise ValueError(f'Runtime file mismatch: {name}')
        with archive.open('rb') as f:
            digest = hashlib.file_digest(f, 'sha256').hexdigest()
        archive.rename(target)
        checksum.write_text(f'{digest}  {NAME}\n', encoding='ascii')
    print(f'OK: {target}\nSHA256: {digest}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist', type=Path, required=True)
    parser.add_argument('--notices', type=Path, required=True, help='Reviewed third-party notices and license materials')
    parser.add_argument('--output', type=Path, default=Path('release'))
    args = parser.parse_args()
    try:
        package(args.dist, args.notices, args.output)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        parser.exit(1, f'Packaging failed: {exc}\n')
