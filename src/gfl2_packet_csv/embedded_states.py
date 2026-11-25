"""Flat integrated state CSV extension; existing base manifest and 44 schemas are unchanged.
State rows bind to the exact base manifest bytes, which in turn bind every base CSV.
The extension manifest binds the state CSV bytes; this is integrity, not authentication.
"""
import hashlib
import json
from pathlib import Path
from .atomic_output import atomic_output, write_verified_zip

STATE_FILES = {'panel_state': 'panel-state.csv', 'equipment_state': 'equipment-state.csv'}
STATE_MANIFEST = 'state_manifest.json'
STATE_MEMBERS = frozenset((*STATE_FILES.values(), STATE_MANIFEST))
LIMITS = {'panel_state': 1024*1024, 'equipment_state': 16*1024*1024}
SCHEMA = 'gf2-embedded-state-1'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def _unique(pairs):
    out = {}
    for k, v in pairs:
        if k in out: raise ValueError('duplicate state metadata')
        out[k] = v
    return out


def read_embedded_states(archive, base_manifest, names):
    found = STATE_MEMBERS & names
    if not found: return {}, ''
    if found != STATE_MEMBERS: raise ValueError('incomplete embedded state inventory')
    if archive.getinfo(STATE_MANIFEST).file_size > 16384:
        raise ValueError('embedded state metadata too large')
    meta = json.loads(archive.read(STATE_MANIFEST).decode('utf-8'), object_pairs_hook=_unique)
    binding = digest(base_manifest)
    if (set(meta) != {'schema', 'base_manifest_sha256', 'files'} or meta['schema'] != SCHEMA
            or meta['base_manifest_sha256'] != binding or set(meta['files']) != set(STATE_FILES.values())):
        raise ValueError('embedded state metadata mismatch')
    out = {}
    for key, name in STATE_FILES.items():
        info = archive.getinfo(name)
        if info.file_size > LIMITS[key]: raise ValueError('embedded state too large')
        data = archive.read(name)
        actual = {'bytes': len(data), 'sha256': digest(data)}
        entry = meta['files'][name]
        if type(entry.get('bytes')) is not int or entry != actual:
            raise ValueError('embedded state digest mismatch')
        out[key] = data
    return out, binding


def bound_state_digest(source, bundle_path, bundle, kind):
    from .bundle_import import bound_archive_digest
    full = bound_archive_digest(bundle_path, bundle)
    if isinstance(source, bytes):
        if getattr(bundle, 'embedded_states', {}).get(kind) != source or not bundle.state_binding_sha256:
            raise ValueError('embedded state source mismatch')
        return bundle.state_binding_sha256
    if getattr(bundle, 'embedded_states', {}):
        raise ValueError('external state rejected for integrated backup')
    return full


def write_integrated_backup(bundle, root, expected, manifest_bytes, panel, equipment, dolls, weapons, attachments):
    """Generate both state members first, publish once; on generation failure keep base backup only."""
    root = Path(root)
    binding = digest(manifest_bytes)
    extra = {}
    status = {key: {'status': 'unavailable'} for key in STATE_FILES}
    try:
        try:
            panel.publish(bundle, dolls, binding_sha=binding, destination=root/STATE_FILES['panel_state'])
            equipment.publish(bundle, weapons, attachments, binding_sha=binding, destination=root/STATE_FILES['equipment_state'])
            for key, name in STATE_FILES.items():
                with (root/name).open('rb') as f: data = f.read(LIMITS[key]+1)
                if len(data) > LIMITS[key]: raise ValueError('state CSV too large')
                extra[name] = {'bytes': len(data), 'sha256': digest(data)}
            meta = json.dumps({'schema': SCHEMA, 'base_manifest_sha256': binding, 'files': extra}, indent=2).encode('utf-8')
            with atomic_output(root/STATE_MANIFEST) as tmp: tmp.write_bytes(meta)
        except (OSError, ValueError):
            write_verified_zip(bundle, root, expected)
        else:
            write_verified_zip(bundle, root, {**expected, **{n:(v['bytes'],v['sha256']) for n,v in extra.items()},
                                             STATE_MANIFEST:(len(meta),digest(meta))})
            status = {key: {'status': 'written', 'member': name} for key,name in STATE_FILES.items()}
        return status
    finally:
        for name in STATE_MEMBERS:
            try: (root/name).unlink(missing_ok=True)
            except OSError: pass
