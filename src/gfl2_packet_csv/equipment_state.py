"""Bundle-bound equipment state observations; never export opaque flag words.
Attachment Lock bit has a paired experiment. Weapon bits and ReadDot remain
candidates. Missing or inconsistent observations are not equivalent to zero.
"""
from dataclasses import dataclass, field
from pathlib import Path
import csv
import hashlib
from .wire_probe import parse_fields, WireDecodeError
from .panel_state import scalar
from .atomic_output import atomic_output

SCHEMA = 'gf2-equipment-state-1'
FIELDS = ('schema', 'bundle_sha256', 'kind', 'uid', 'model_id', 'observation',
          'lock_candidate', 'read_dot_candidate', 'skin_use_candidate')
PACKETS = {11021: ('weapon', 7), 11061: ('attachment', 6)}
MODELS = {'weapon': ('weapons.csv', 'item_id'), 'attachment': ('attachments.csv', 'part_id')}
UNKNOWN = ('unavailable', None, None, None)


def normalize(kind, raw):
    if kind not in MODELS:
        raise ValueError('invalid equipment kind')
    if raw is None:
        return ('field_absent', None, None, None)
    mask = 7 if kind == 'weapon' else 3
    if type(raw) is not int or raw < 0 or raw & ~mask:
        return ('unknown_bits', None, None, None)
    return ('observed' if raw else 'explicit_zero', raw & 1, (raw >> 1) & 1,
            (raw >> 2) & 1 if kind == 'weapon' else None)


@dataclass
class EquipmentObservations:
    records: dict = field(default_factory=dict)
    conflicts: set = field(default_factory=set)
    invalid: set = field(default_factory=set)

    def observe(self, packet, raw):
        if packet not in PACKETS:
            return
        kind, field_number = PACKETS[packet]
        try:
            current = {}
            for n, w, value in parse_fields(raw, max_fields=60000):
                if n != 1:
                    continue  # owner links are not state records
                if w != 2:
                    raise ValueError('invalid equipment envelope')
                record = parse_fields(value, max_fields=5000)
                uid = scalar(record, 1)
                model = scalar(record, 2)
                if not uid or uid in current or model == 0:
                    raise ValueError('invalid equipment identity')
                # A sparse record without model identity cannot validate a
                # model-bound state; do not silently reuse an older state.
                current[uid] = (model, normalize(kind, scalar(record, field_number)) if model is not None else UNKNOWN)
            for uid, value in current.items():
                key = (kind, uid)
                if key in self.records and self.records[key] != value:
                    self.conflicts.add(key)
                self.records[key] = value
        except (WireDecodeError, ValueError, TypeError, OverflowError, IndexError):
            self.invalid.add(kind)

    def publish(self, bundle, weapons, attachments, *, binding_sha=None, destination=None):
        bundle = Path(bundle)
        destination = Path(destination) if destination is not None else bundle.with_suffix('.equipment-state.csv')
        sha = binding_sha
        if sha is None:
            with bundle.open('rb') as f:
                sha = hashlib.file_digest(f, 'sha256').hexdigest()
        with atomic_output(destination) as tmp:
            with tmp.open('w', encoding='utf-8-sig', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(FIELDS)
                for kind, inventory in [('weapon', weapons), ('attachment', attachments)]:
                    model_field = MODELS[kind][1]
                    for uid, record in sorted(inventory.items()):
                        model = record[model_field]
                        key = (kind, uid)
                        observed = self.records.get(key)
                        state = UNKNOWN if (kind in self.invalid or key in self.conflicts or
                                             observed is None or observed[0] != model) else observed[1]
                        writer.writerow((SCHEMA, sha, kind, uid, model, *state))
        return destination


def read_equipment_state(path, bundle_path, bundle=None):
    """Validate a complete inventory, matching ZIP hash and normalized bit values.
    This verifies correspondence, not authenticity of externally edited CSVs.
    """
    from .bundle_import import read_bundle, bound_archive_digest
    from .panel_references import uint, csv_snapshot
    if bundle is None:
        bundle = read_bundle(bundle_path)
    if not bundle.complete_capture:
        raise ValueError('complete bundle required')
    expected = {}
    for kind, (name, model_field) in MODELS.items():
        for r in bundle.rows[name]:
            key = (kind, uint(r['uid']))
            model = uint(r[model_field])
            if key in expected or not key[1] or not model:
                raise ValueError('invalid equipment inventory')
            expected[key] = model
    from .embedded_states import bound_state_digest
    sha = bound_state_digest(path, bundle_path, bundle, 'equipment_state')
    out = {}
    with csv_snapshot(path, 16 * 1024 * 1024) as f:
        reader = csv.DictReader(f)
        if tuple(reader.fieldnames or ()) != FIELDS:
            raise ValueError('equipment state schema mismatch')
        for r in reader:
            if None in r or any(v is None for v in r.values()) or r['schema'] != SCHEMA or r['bundle_sha256'] != sha:
                raise ValueError('equipment state bundle mismatch')
            kind = r['kind']; key = (kind, uint(r['uid'])); model = uint(r['model_id'])
            if key in out or expected.get(key) != model:
                raise ValueError('equipment state identity mismatch')
            status = r['observation']
            bits = tuple(uint(r[k], optional=True) for k in FIELDS[-3:])
            if status in ('observed', 'explicit_zero'):
                required = bits if kind == 'weapon' else bits[:2]
                if any(v not in (0, 1) for v in required) or (kind == 'attachment' and bits[2] is not None):
                    raise ValueError('invalid equipment bits')
                if (status == 'explicit_zero') != (not any(required)):
                    raise ValueError('inconsistent equipment state')
            elif status not in ('field_absent', 'unknown_bits', 'unavailable') or any(v is not None for v in bits):
                raise ValueError('invalid equipment observation')
            out[key] = {'kind': kind, 'uid': key[1], 'model_id': model, 'observation': status,
                        'lock_candidate': bits[0], 'read_dot_candidate': bits[1], 'skin_use_candidate': bits[2],
                        'lock_provenance': ('paired_attachment_bit' if kind == 'attachment' and bits[0] is not None
                                            else 'weapon_bit_candidate' if bits[0] is not None else 'unknown')}
    if not out or set(out) != set(expected):
        raise ValueError('equipment state coverage mismatch')
    return {'schema': SCHEMA, 'attachments': [v for (k, _), v in sorted(out.items()) if k == 'attachment'],
            'weapons': [v for (k, _), v in sorted(out.items()) if k == 'weapon']}
