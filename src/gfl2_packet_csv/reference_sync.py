"""GUI-only public reference sync. Never receives account or packet data.

A configured HTTPS API is the trust anchor; publisher SHA-256 is integrity,
not a digital signature. No redirects, cookies or upload requests are used.
"""
from dataclasses import dataclass
from pathlib import Path
import json
import http.client
import os
import re
import tempfile
import urllib.request
import urllib.parse
import urllib.error
from .reference_data import (BASELINE_SHA256, baseline, load_index, parse_index,
                             use_snapshot, _unique)

# Same public static origin as GF2Tools remote_catalog.py.
DEFAULT_API_BASE = "https://raw.githubusercontent.com/Kimbyeoungjang/GF2Tools-api/main/api/v1"
MAX_METADATA = 256 * 1024
_FAILURES = (OSError, ValueError, TypeError, KeyError, ArithmeticError, EOFError, RecursionError, http.client.HTTPException)
SEED_INDEX = Path(__file__).resolve().parent / "reference_seed" / "reference_data.json"


@dataclass(frozen=True)
class Selection:
    index: Path | None
    identity: str
    status: str


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def normalize_base(value):
    u = urllib.parse.urlsplit(value.strip().rstrip('/'))
    if (u.scheme != 'https' or not u.hostname or u.username or u.password
            or u.query or u.fragment or u.port not in (None, 443)
            or not u.path.endswith('/api/v1')
            or any(p in ('', '.', '..') for p in u.path.split('/')[1:])
            or not re.fullmatch(r'[A-Za-z0-9.-]+', u.hostname)
            or not re.fullmatch(r'/[A-Za-z0-9._/-]+', u.path)):
        raise ValueError('expected trusted HTTPS /api/v1 URL')
    return u.geturl()


def configured_base(root):
    p = Path(root) / 'reference_api_url.txt'
    if p.is_file():
        with p.open('r', encoding='utf-8-sig') as f:
            value = f.read(2049).strip()
        if len(value) > 2048:
            raise ValueError('reference API URL too long')
    else:
        value = DEFAULT_API_BASE
    return normalize_base(value) if value else ''


def _get(url, limit):
    request = urllib.request.Request(url, method='GET', headers={
        'User-Agent': 'GFL2-CSV-Exporter-reference/1.6.0', 'Accept-Encoding': 'identity'})
    # No ambient proxy credentials, no cookies, no cross-origin redirects.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    with opener.open(request, timeout=5) as response:
        if response.geturl() != url:
            raise ValueError('reference redirect rejected')
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('reference response too large')
    return data


def _json(raw):
    value = json.loads(raw, object_pairs_hook=_unique)
    if not isinstance(value, dict):
        raise ValueError('reference metadata must be an object')
    return value


def _encoded(entry):
    return (json.dumps(entry, sort_keys=True, indent=2) + '\n').encode('utf-8')


def _atomic(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.reference-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        try:
            Path(name).unlink(missing_ok=True)
        except OSError:
            pass


def _version_path(cache, entry):
    return cache / 'versions' / entry['reference_sha256'] / entry['sha256'] / 'reference_data.json'


def _validate(index):
    snap = load_index(index)
    # Catalog functions intentionally fail closed on unsupported game structures.
    # Do not activate packs that make previously supported catalogs unavailable.
    from . import (item_catalog, doll_stat_catalog, helix_effect_catalog,
                   progression_reference_catalog, support_combat_catalog,
                   counter_rule_catalog, task_reference_catalog, panel_references,
                   attachment_affixes, calibration_catalog, common_key_enhancement_catalog,
                   growth_reference_catalog, static_list_catalog, static_pair_catalog,
                   support_effect_catalog, support_stage_candidates, support_catalog,
                   support_attribute_totals, weapon_property_catalog, weapon_growth_catalog,
                   remolding_catalog, ui_reference_catalog, key_catalog)
    # Research policy is not game Table data. A changed policy requires a
    # reviewed Exporter release, not an automatic data update.
    for name, body in baseline().data.items():
        if Path(name).name.startswith('protocol_') and snap.data[name] != body:
            raise ValueError('unsupported protocol policy')
    with use_snapshot(snap):
        checks = (item_catalog.catalog(), doll_stat_catalog.catalog(),
                  helix_effect_catalog.catalog(), progression_reference_catalog.helix_progression_catalog(),
                  progression_reference_catalog.support_progression_catalog(), support_combat_catalog.combat_catalog(),
                  counter_rule_catalog.counter_rules(), counter_rule_catalog.condition_references(),
                  counter_rule_catalog.challenge_references(), task_reference_catalog.task_references(),
                  panel_references.read_static_csv(), panel_references.read_doll_food_groups(),
                  *attachment_affixes.catalogs(), *attachment_affixes.upgrade_catalogs(),
                  attachment_affixes.value_types(), calibration_catalog.catalog(),
                  common_key_enhancement_catalog.base_catalog(), common_key_enhancement_catalog.copy_catalog(),
                  growth_reference_catalog.catalog(), static_list_catalog.catalog(), static_pair_catalog.catalog(),
                  support_effect_catalog.effect_catalog(), support_stage_candidates.thresholds(),
                  support_catalog.catalogs(), *support_catalog.group_references(), support_catalog.frame_slots(),
                  support_catalog.room_levels(), support_catalog.special_break_records(),
                  support_attribute_totals.secondary_values(), support_attribute_totals.level_totals(),
                  weapon_property_catalog.base_catalog(), weapon_property_catalog.level_catalog(),
                  weapon_property_catalog.break_catalog(), weapon_growth_catalog.limits(),
                  remolding_catalog.models(), remolding_catalog.options(), remolding_catalog.primary_attributes(),
                  remolding_catalog.secondary_attribute_counts(), ui_reference_catalog.costume_catalog(),
                  key_catalog.catalog())
        if not all(checks):
            raise ValueError('unsupported reference catalog')
    return snap


def _read_entry(path):
    with path.open('rb') as f:
        return parse_index(f.read(8193))


def _install(cache, entry, raw):
    index = _version_path(cache, entry)
    index.parent.mkdir(parents=True, exist_ok=True)
    # Never overwrite a previously valid snapshot. Repair a corrupt one only
    # after the replacement has passed verification in a private directory.
    with tempfile.TemporaryDirectory(prefix='.reference-stage-', dir=cache) as stage:
        stage = Path(stage)
        (stage / entry['file']).write_bytes(raw)
        (stage / 'reference_data.json').write_bytes(_encoded(entry))
        snap = _validate(stage / 'reference_data.json')
        if snap.sha256 != entry['reference_sha256']:
            raise ValueError('reference identity mismatch')
        try:
            if _read_entry(index) != entry or load_index(index).sha256 != snap.sha256:
                raise ValueError('cached entry mismatch')
        except (OSError, ValueError):
            _atomic(index.parent / entry['file'], raw)
            _atomic(index, _encoded(entry))
        if load_index(index).sha256 != snap.sha256:
            raise ValueError('installed snapshot mismatch')
        _atomic(cache / 'active.json', _encoded(entry))
    return Selection(index, snap.sha256, 'updated')


def _local(cache):
    # Active snapshot first, then local manual pack, then shipped approved
    # pack. Copy the seed into the versioned cache so app upgrades retain it.
    invalid = False
    for pointer, active in ((cache / 'active.json', True), (cache / 'reference_data.json', False), (SEED_INDEX, False)):
        try:
            entry = _read_entry(pointer)
            index = _version_path(cache, entry) if active else pointer
            if _read_entry(index) != entry:
                raise ValueError('cached index mismatch')
            snap = _validate(index)
            if not active:
                try:
                    cache.mkdir(parents=True, exist_ok=True)
                    with (index.parent / entry['file']).open('rb') as f:
                        raw = f.read(entry['bytes'] + 1)
                    selected = _install(cache, entry, raw)
                    return Selection(selected.index, snap.sha256, 'fallback' if invalid else 'local')
                except _FAILURES:
                    # Read-only installations may still use the validated seed.
                    pass
            return Selection(index, snap.sha256, 'fallback' if invalid else ('cached' if active else 'local'))
        except _FAILURES:
            invalid |= pointer.exists()
            continue
    return Selection(None, BASELINE_SHA256, 'fallback' if invalid else 'baseline')


def _approved(base, get):
    manifest = _json(get(base + '/manifest.json', MAX_METADATA))
    ref = manifest.get('exporter_reference')
    if (manifest.get('schema_version') != 2 or not isinstance(ref, dict) or ref.get('status') != 'approved'
            or ref.get('latest') != 'exporter-reference/latest.json'
            or ref.get('approval') != 'exporter-reference/approval.json'):
        raise ValueError('no current approved reference')
    prefix = base + '/exporter-reference/'
    entry = parse_index(get(prefix + 'latest.json', 8192))
    approval = _json(get(prefix + 'approval.json', 8192))
    identity = entry['reference_sha256']
    if (ref.get('reference_sha256') != identity or approval.get('reference_sha256') != identity
            or approval.get('schema') != 'gf2-reference-approval-1' or approval.get('status') != 'approved'
            or not manifest.get('source_table_version')
            or ref.get('source_table_version') != manifest['source_table_version']
            or approval.get('source_table_version') != manifest['source_table_version']
            or not manifest.get('reference_input') or approval.get('reference_input') != manifest['reference_input']
            or approval.get('manual_policy_version') != 'v35-policy.1'
            or not isinstance(approval.get('static_code_sha256'), str)
            or not re.fullmatch('[0-9a-f]{64}', approval['static_code_sha256'])
            or ('reference_static_code_sha256' in manifest and
                approval['static_code_sha256'] != manifest['reference_static_code_sha256'])):
        raise ValueError('reference approval mismatch')
    immutable = parse_index(get(prefix + identity + '/reference_data.json', 8192))
    if immutable != entry:
        raise ValueError('reference index mismatch')
    return prefix, entry


def refresh(root, *, get=_get):
    """Select one immutable index before the helper starts; never network in helper."""
    root = Path(root)
    cache = root / 'reference_data'
    fallback = _local(cache)
    try:
        base = configured_base(root)
        if not base:
            return fallback
        prefix, entry = _approved(base, get)
        cache.mkdir(parents=True, exist_ok=True)
        index = _version_path(cache, entry)
        try:
            if _read_entry(index) != entry:
                raise ValueError('cached index mismatch')
            snap = _validate(index)
        except _FAILURES:
            raw = get(prefix + entry['reference_sha256'] + '/' + entry['file'], entry['bytes'])
            return _install(cache, entry, raw)
        _atomic(cache / 'active.json', _encoded(entry))
        return Selection(index, snap.sha256, 'current')
    except _FAILURES:
        return Selection(fallback.index, fallback.identity, 'fallback')
