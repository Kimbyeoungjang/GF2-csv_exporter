"""Versioned, immutable CSV catalog snapshots; no network or executable content.
The packaged V35 baseline is retained for unversioned historic backups.
A reference pack is trusted only when its archive digest is obtained separately
from a trusted publisher. Hashes establish integrity, not authenticity.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache as _cache, wraps
from importlib.resources import files as _resources
from types import MappingProxyType
from pathlib import Path
import csv
import hashlib
import io
import json
import zipfile
import re
import zlib
import lzma

SCHEMA = 'gf2-reference-csv-1'
GROUPS = ('static_tables', 'static_achievements')
MAX_PACK = 16 * 1024 * 1024
MAX_MEMBER = 8 * 1024 * 1024


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _inventory(data):
    return {name: {'bytes': len(body), 'sha256': _sha(body)} for name, body in sorted(data.items())}


def _identity(data):
    return _sha(json.dumps(_inventory(data), sort_keys=True, separators=(',', ':')).encode('ascii'))


@dataclass(frozen=True, eq=False)
class Snapshot:
    data: object
    sha256: str

    def metadata(self):
        return {'schema': SCHEMA, 'sha256': self.sha256}


def _snapshot(data):
    return Snapshot(MappingProxyType(dict(data)), _identity(data))


@_cache(maxsize=1)
def baseline():
    data = {}
    for group in GROUPS:
        for resource in _resources(__package__ + '.' + group).iterdir():
            if resource.name.endswith('.csv'):
                data[group + '/' + resource.name] = resource.read_bytes()
    snap = _snapshot(data)
    if snap.sha256 != BASELINE_SHA256:
        raise ValueError('bundled reference baseline mismatch')
    return snap


BASELINE_SHA256 = '77640aa4110039ad561ee6a2cafd53ee9eb6acc1aa17bbcea8a65ad560a7f40f'
_selected = ContextVar('gf2_reference_snapshot', default=None)
_process_default = None


def current():
    return _selected.get() or _process_default or baseline()


@contextmanager
def use_snapshot(snapshot):
    token = _selected.set(snapshot)
    try:
        yield snapshot
    finally:
        _selected.reset(token)


def select_process_snapshot(snapshot):
    """Call once at CLI/helper startup, before capture threads or decoding."""
    global _process_default
    if _process_default is not None:
        raise ValueError('reference snapshot already selected')
    _process_default = snapshot


class ReferenceMismatch(ValueError):
    """The backup requires a different, trusted reference snapshot."""


def require_manifest(manifest):
    expected = current().metadata()
    if 'reference_data' not in manifest:
        if current().sha256 != BASELINE_SHA256:
            raise ReferenceMismatch('legacy backup requires V35 baseline reference data')
    elif manifest['reference_data'] != expected:
        raise ReferenceMismatch('reference data mismatch: select the matching trusted reference pack')


def _unique(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError('duplicate reference metadata')
        out[k] = v
    return out


def load_pack(path, expected_sha256):
    """Load only the exact expected CSV inventory, without extracting files."""
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
        raise ValueError('trusted reference archive SHA-256 required')
    with Path(path).open('rb') as f:
        raw = f.read(MAX_PACK + 1)
    if len(raw) > MAX_PACK or _sha(raw) != expected_sha256.lower():
        raise ValueError('reference archive digest mismatch')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            infos = z.infolist()
            names = [i.filename for i in infos]
            base = baseline().data
            if len(names) != len(set(names)) or set(names) != set(base) | {'reference_manifest.json'}:
                raise ValueError('unexpected reference inventory')
            if any(i.is_dir() or i.flag_bits & 1 or i.file_size > MAX_MEMBER for i in infos) or sum(i.file_size for i in infos) > MAX_PACK:
                raise ValueError('reference archive limits exceeded')
            if z.getinfo('reference_manifest.json').file_size > 65536:
                raise ValueError('reference manifest too large')
            meta = json.loads(z.read('reference_manifest.json'), object_pairs_hook=_unique)
            data = {n: z.read(n) for n in base}
        snap = _snapshot(data)
        if meta != {**snap.metadata(), 'files': _inventory(data)}:
            raise ValueError('reference manifest mismatch')
        # A pack cannot silently replace a catalog with another CSV schema.
        for name, body in data.items():
            def rows(b):
                return csv.reader(io.StringIO(b.decode('utf-8-sig'), newline=''), strict=True)
            reader = rows(body)
            header = next(reader, None)
            if not header or header != next(rows(base[name]), None):
                raise ValueError('reference CSV header mismatch')
            if any(len(r) != len(header) for r in reader):
                raise ValueError('reference CSV row width mismatch')
        return snap
    except (zipfile.BadZipFile, RuntimeError, UnicodeError, csv.Error, TypeError, KeyError, EOFError, zlib.error, lzma.LZMAError, OverflowError) as exc:
        raise ValueError('invalid reference pack') from exc


class _Resource:
    def __init__(self, name):
        self.name = name
    def joinpath(self, name):
        return _Resource(self.name + '/' + name)
    __truediv__ = joinpath
    def open(self, mode='r', *, encoding='utf-8', newline=None):
        if mode not in ('r', 'rt'):
            raise ValueError('read-only reference resource')
        try:
            data = current().data[self.name]
        except KeyError as exc:
            raise FileNotFoundError(self.name) from exc
        return io.StringIO(data.decode(encoding), newline=newline)


def files(package):
    group = package.rsplit('.', 1)[-1]
    if group not in GROUPS:
        raise ValueError('unexpected reference resource group')
    return _Resource(group)


def lru_cache(maxsize=1):
    """Partition all catalog-dependent memoization by immutable snapshot."""
    def decorate(fn):
        @_cache(maxsize=None if maxsize is None else maxsize * 4)
        def cached(snapshot, args, kwargs):
            with use_snapshot(snapshot):
                return fn(*args, **dict(kwargs))
        @wraps(fn)
        def wrapper(*args, **kwargs):
            return cached(current(), args, tuple(kwargs.items()))
        wrapper.cache_clear = cached.cache_clear
        wrapper.cache_info = cached.cache_info
        return wrapper
    return decorate


def parse_index(raw):
    """Validate the publisher's bounded index before using any path."""
    if len(raw) > 8192:
        raise ValueError('reference index too large')
    try:
        entry = json.loads(raw, object_pairs_hook=_unique)
        if not isinstance(entry, dict) or set(entry) != {'schema', 'reference_sha256', 'file', 'sha256', 'bytes', 'account_contract'}:
            raise ValueError('invalid reference index')
        identity, sha = entry['reference_sha256'], entry['sha256']
        if (entry['schema'] != SCHEMA or type(entry['account_contract']) is not int or entry['account_contract'] != 35
                or not all(isinstance(x, str) and re.fullmatch('[0-9a-f]{64}', x) for x in (identity, sha))
                or entry['file'] != f'gf2-reference-{identity}.zip'
                or type(entry['bytes']) is not int or not 0 < entry['bytes'] <= MAX_PACK):
            raise ValueError('invalid reference index fields')
        return entry
    except (UnicodeError, TypeError, KeyError) as exc:
        raise ValueError('invalid reference index') from exc


def load_index(path):
    """Read a trusted locally installed publisher index, not a backup member."""
    path = Path(path)
    with path.open('rb') as f:
        entry = parse_index(f.read(8193))
    pack = path.parent / entry['file']
    if pack.stat().st_size != entry['bytes']:
        raise ValueError('reference size mismatch')
    snapshot = load_pack(pack, entry['sha256'])
    if snapshot.sha256 != entry['reference_sha256']:
        raise ValueError('reference identity mismatch')
    return snapshot
