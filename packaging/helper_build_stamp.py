"""Local build freshness check. Not a signature or trust check."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def fingerprint(root=ROOT):
    package = root / 'src/gfl2_packet_csv'
    paths = [root / 'pyproject.toml', *sorted((root / 'packaging').glob('*.py')),
             *sorted((root / 'packaging').glob('*.spec')),
             *sorted(package.rglob('*.py')), *sorted(package.rglob('*.csv'))]
    if not (package / '__init__.py').is_file() or not (root / 'packaging/helper.spec').is_file():
        raise ValueError('build inputs missing')
    dependencies = {name: importlib.metadata.version(name) for name in
                    ('pyinstaller', 'pydivert', 'psutil')}
    data = {'python': sys.version, 'platform': sys.platform, 'bits': 64 if sys.maxsize > 2**32 else 32,
            'dependencies': dependencies,
            'inputs': {p.relative_to(root).as_posix(): sha(p) for p in paths}}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def verify(exe, stamp, root=ROOT):
    try:
        return json.loads(stamp.read_text(encoding='utf-8')) == {
            'schema': 1, 'input_sha256': fingerprint(root), 'exe_sha256': sha(exe)}
    except (OSError, ValueError, importlib.metadata.PackageNotFoundError):
        return False


def write(exe, stamp, expected, root=ROOT):
    current = fingerprint(root)
    if current != expected:
        raise ValueError('build inputs changed')
    temp = stamp.with_suffix(stamp.suffix + '.tmp')
    try:
        temp.write_text(json.dumps({'schema': 1, 'input_sha256': current,
                                    'exe_sha256': sha(exe)}, sort_keys=True), encoding='utf-8')
        os.replace(temp, stamp)
    finally:
        temp.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['fingerprint', 'verify', 'write'])
    parser.add_argument('exe', type=Path, nargs='?')
    parser.add_argument('stamp', type=Path, nargs='?')
    parser.add_argument('expected', nargs='?')
    args = parser.parse_args()
    try:
        if args.action == 'fingerprint':
            print(fingerprint())
        elif args.action == 'verify':
            return 0 if verify(args.exe, args.stamp) else 1
        else:
            write(args.exe, args.stamp, args.expected)
    except (OSError, ValueError, importlib.metadata.PackageNotFoundError):
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
