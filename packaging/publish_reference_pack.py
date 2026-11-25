"""Create an immutable API-ready CSV pack and a local/API index entry.
Run from any directory; source must contain both normalized CSV directories.
No account data, executable files, or game installation access.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from gfl2_packet_csv.reference_data import baseline, load_pack, _snapshot, _inventory, SCHEMA


def publish(source, output):
    source, output = Path(source), Path(output)
    base = baseline()
    inventory = {p.relative_to(source).as_posix() for group in ('static_tables', 'static_achievements')
                 for p in (source / group).glob('*.csv')}
    if inventory != set(base.data):
        raise ValueError('reference source inventory mismatch')
    data = {name: (source / name).read_bytes() for name in base.data}
    snapshot = _snapshot(data)
    name = f'gf2-reference-{snapshot.sha256}.zip'
    output.mkdir(parents=True, exist_ok=True)
    pack = output / name
    if pack.exists():
        raise FileExistsError(pack)
    try:
        with zipfile.ZipFile(pack, 'x', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for n, b in sorted(data.items()):
                z.writestr(zipfile.ZipInfo(n), b, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
            z.writestr(zipfile.ZipInfo('reference_manifest.json'), json.dumps({**snapshot.metadata(), 'files': _inventory(data)}, sort_keys=True), compress_type=zipfile.ZIP_DEFLATED)
        sha = hashlib.sha256(pack.read_bytes()).hexdigest()
        load_pack(pack, sha)
    except Exception:
        pack.unlink(missing_ok=True)
        raise
    entry = {'schema': SCHEMA, 'reference_sha256': snapshot.sha256, 'file': name,
             'sha256': sha, 'bytes': pack.stat().st_size, 'account_contract': 35}
    # This index describes a single reviewed release; the API retains all immutable packs.
    (output / 'reference_data.json').write_text(json.dumps(entry, indent=2) + '\n', encoding='utf-8')
    return entry


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path)
    p.add_argument('output', type=Path)
    a = p.parse_args()
    print(json.dumps(publish(a.source, a.output), indent=2))
