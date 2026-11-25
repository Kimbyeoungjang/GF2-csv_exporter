"""Build a public source ZIP from allowlisted project files, not a working folder.

Checks are deliberately conservative heuristics, not proof of anonymity.
Review any flagged source locally; never print the matching secret value.
"""
from pathlib import Path
import argparse
import re
import stat
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = 'src/gfl2_packet_csv'
ROOT_FILES = {'.gitignore', 'LICENSE', 'README.md', 'pyproject.toml',
              'build_exe.bat', 'start_packet_csv_exporter.bat',
              'GF2TOOLS_IMPORT_CONTRACT_V35_ko.md', 'REFERENCE_DATA_CONTRACT_ko.md',
              'RELEASE_CHECKLIST_ko.md', 'RELEASE_NOTES_ko.md', 'VALIDATION_ko.md',
              'PUBLIC_RELEASE_AUDIT_ko.md'}
_SUSPECT = {
    'private-key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----'),
    'access-token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[A-Z0-9]{16}|xox[baprs]-[A-Za-z0-9-]{15,})\b'),
    'absolute-user-path': re.compile(r'(?i)(?:[A-Z]:[\\/]+Users[\\/]+|/(?:home|Users|mnt/data)/)[^\s\\/"\']+'),
    'email': re.compile(r'(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b'),
}
# Test examples are explicit reserved/fictitious values; no blanket exemption.
_EXAMPLE_PATH = re.compile(r'(?i)(?:[A-Z]:[\\/]+Users[\\/]+private|test@example\.com|evil\.invalid@host\.invalid)\b')


def plain(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('link/reparse point in public source: ' + path.name)
    return info


def scan_text(name, text):
    # Match values are intentionally never included in exceptions or reports.
    if name.startswith('tests/'):
        text = _EXAMPLE_PATH.sub('[synthetic]', text)
    for kind, pattern in _SUSPECT.items():
        if pattern.search(text):
            raise ValueError(f'{kind} requires review: {name}')


def collect(root=ROOT):
    root = Path(root)
    paths = [root / n for n in sorted(ROOT_FILES) if (root / n).exists()]
    for folder, extensions in (('packaging', {'.py', '.spec'}),
                               ('tests', {'.py'}), (PACKAGE, {'.py'})):
        directory = root / folder
        if not stat.S_ISDIR(plain(directory).st_mode):
            raise ValueError('invalid source directory')
        paths += [p for p in directory.iterdir() if p.suffix in extensions]
    for name in ('manifest.json', 'approval.json'):
        paths.append(root / 'tests/fixtures' / name)
    sys.path.insert(0, str(root / 'src'))
    from gfl2_packet_csv.reference_data import baseline, load_index
    # The exact CSV inventory is part of the historic reference identity.
    snapshot = baseline()
    for name in snapshot.data:
        paths.append(root / PACKAGE / name)
    for group in ('static_tables', 'static_achievements'):
        paths.append(root / PACKAGE / group / '__init__.py')
    seed = root / PACKAGE / 'reference_seed' / 'reference_data.json'
    load_index(seed)
    import json
    paths += [seed, seed.parent / json.loads(seed.read_text(encoding='utf-8'))['file']]
    entries = {}
    for path in paths:
        name = path.relative_to(root).as_posix()
        parent = path.parent
        while parent != root:
            if not stat.S_ISDIR(plain(parent).st_mode):
                raise ValueError('invalid source parent: ' + name)
            parent = parent.parent
        if not stat.S_ISREG(plain(path).st_mode):
            raise ValueError('not a source file: ' + name)
        raw = path.read_bytes()
        if path.suffix != '.zip' and name != 'LICENSE':
            # The trusted reference ZIP is validated above and its CSVs below.
            scan_text(name, raw.decode('utf-8-sig'))
        if name in entries:
            raise ValueError('duplicate source path')
        entries[name] = raw
    with zipfile.ZipFile(seed.parent / json.loads(seed.read_text(encoding='utf-8'))['file']) as z:
        for name in z.namelist():
            scan_text('reference_seed/' + name, z.read(name).decode('utf-8-sig'))
    return entries


def build(output, root=ROOT):
    entries = collect(root)
    output = Path(output)
    with output.open('xb') as f, zipfile.ZipFile(f, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for name, raw in sorted(entries.items()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            z.writestr(info, raw, compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)
    with zipfile.ZipFile(output) as z:
        if z.testzip() or set(z.namelist()) != set(entries):
            raise ValueError('source ZIP verification failed')
        if any(z.read(name) != raw for name, raw in entries.items()):
            raise ValueError('source ZIP content mismatch')
    return len(entries)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(f'Public source files: {build(args.output)}')
