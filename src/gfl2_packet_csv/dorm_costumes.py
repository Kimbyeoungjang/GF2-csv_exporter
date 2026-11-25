"""Catalog-gated observation of 11627.1.3, not proof of a dorm roster.
Only verified game reference IDs leave this module; unknown scalars are not
retained. Outer and inner doll IDs must agree. No raw packet storage.
"""
from __future__ import annotations
import csv
from dataclasses import dataclass
from .reference_data import lru_cache
from .reference_data import files
from .account_extension import parse, records, msg, ident, scalar

FILE = 'dorm_doll_costumes.csv'
HEADERS = ['doll_id', 'costume_id', 'costume_reference_status', 'dorm_reference_match_candidate']
COSTUME_STATES = ('catalog_verified', 'field_absent', 'explicit_zero', 'unrecognized')
REFERENCE_STATES = ('catalog_roster_match', 'field_absent', 'explicit_zero', 'unrecognized', 'invalid_field', 'conflict', 'projection_unavailable')

@lru_cache(maxsize=1)
def known_doll_costumes() -> frozenset[tuple[int, int]]:
    try:
        resource = files('gfl2_packet_csv.static_tables').joinpath('doll_costume_reference_ids.csv')
        with resource.open('r', encoding='utf-8-sig', newline='') as fp:
            return frozenset((int(r['doll_id']), int(r['costume_id'])) for r in csv.DictReader(fp))
    except (OSError, ValueError, KeyError, TypeError):
        return frozenset()


def decode_dorm_costumes(data: bytes) -> tuple[dict, ...] | None:
    outer = msg(parse(data), 1)
    if 3 not in outer:
        return None  # no proof of an empty roster
    result = []
    seen = set()
    catalog = known_doll_costumes()
    for record in records(outer, 3):
        doll_id = ident(record, 1)
        inner = msg(record, 2)
        if doll_id in seen or ident(inner, 1) != doll_id:
            raise ValueError('invalid dorm doll reference')
        seen.add(doll_id)
        skin = scalar(inner, 8)
        if skin is None:
            status = 'field_absent'
        elif skin == 0:
            status = 'explicit_zero'
        elif (doll_id, skin) in catalog:
            status = 'catalog_verified'
        else:
            status = 'unrecognized'
        result.append({'doll_id': doll_id,
                       'costume_id': skin if status == 'catalog_verified' else None,
                       'costume_reference_status': status})
    return tuple(sorted(result, key=lambda r: r['doll_id']))

def reference_candidate(data, rows):
    """11627.1.9 is a verified roster ID candidate, not an active-doll claim."""
    try:
        ref = scalar(msg(parse(data), 1), 9)
    except (ValueError, TypeError, KeyError, IndexError, OverflowError):
        return None, 'invalid_field'
    if ref is None: return None, 'field_absent'
    if ref == 0: return None, 'explicit_zero'
    if ref not in {r['doll_id'] for r in rows}: return None, 'unrecognized'
    from .helix_catalog import catalog
    if ref not in catalog(): return None, 'unrecognized'
    return ref, 'catalog_roster_match'


@dataclass
class DormCostumeObservation:
    rows: tuple[dict, ...] | None = None
    status: str = 'missing'
    reference: tuple | None = None

    def observe(self, data: bytes) -> None:
        if self.status in ('invalid', 'conflict'):
            return
        try:
            rows = decode_dorm_costumes(data)
        except (ValueError, TypeError, KeyError, IndexError, OverflowError):
            self.rows = None
            self.status = 'invalid'
            return
        if rows is None:
            if self.status == 'missing':
                self.status = 'not_observed'
            return
        if self.rows is not None and rows != self.rows:
            self.rows = None
            self.status = 'conflict'
            return
        candidate = reference_candidate(data, rows)
        if self.reference is None:
            self.reference = candidate
        elif self.reference != candidate:
            self.reference = (None, 'conflict')
        self.rows = rows
        self.status = 'observed'

    def projection(self, *, dolls: dict, doll_snapshot_ok: bool):
        state = self.status
        rows = self.rows or ()
        if state == 'observed':
            if not doll_snapshot_ok:
                state = 'doll_snapshot_unavailable'
            elif any(r['doll_id'] not in dolls for r in rows):
                state = 'doll_reference_mismatch'
        ref, ref_state = (self.reference or (None, 'field_absent')) if state == 'observed' else (None, 'projection_unavailable')
        out = [{**r, 'dorm_reference_match_candidate': int(r['doll_id'] == ref) if ref_state == 'catalog_roster_match' else None} for r in rows] if state == 'observed' else []
        counts = {s: sum(r['costume_reference_status'] == s for r in out)
                  for s in COSTUME_STATES}
        return out, {'status': state, 'file': FILE if state == 'observed' else None,
                     'rows': len(out), 'costume_reference_counts': counts,
                     'dorm_reference_status': ref_state,
                     'catalog_total_references': len(known_doll_costumes()),
                     'semantic_status': 'catalog_reference_not_active_costume_proof'}



def validate_projection(rows, meta, owned, *, present, doll_available, dorm_received, uint):
    """Recheck projection even when CSV digests are recomputed."""
    state = meta['status']
    if state not in {'missing','not_observed','invalid','conflict','observed','doll_snapshot_unavailable','doll_reference_mismatch'}:
        raise ValueError('invalid dorm observation status')
    if present != (state == 'observed') or meta['file'] != (FILE if present else None):
        raise ValueError('dorm file availability mismatch')
    if type(meta['rows']) is not int or meta['rows'] != len(rows) or (not present and rows):
        raise ValueError('dorm row count mismatch')
    if present and (not doll_available or not dorm_received or not rows):
        raise ValueError('dorm projection without observed snapshots')
    if meta['semantic_status'] != 'catalog_reference_not_active_costume_proof' or meta['catalog_total_references'] != len(known_doll_costumes()):
        raise ValueError('dorm catalog metadata mismatch')
    ref_state = meta['dorm_reference_status']
    if ref_state not in REFERENCE_STATES or (ref_state == 'projection_unavailable') == present:
        raise ValueError('invalid dorm reference status')
    counts = dict.fromkeys(COSTUME_STATES, 0)
    seen = set()
    matched = 0
    for row in rows:
        did = uint(row['doll_id'], positive=True)
        if did in seen or did not in owned:
            raise ValueError('invalid dorm doll reference')
        seen.add(did)
        status = row['costume_reference_status']
        if status not in counts:
            raise ValueError('invalid dorm costume status')
        counts[status] += 1
        skin = uint(row['costume_id'], optional=True, positive=True)
        if (skin is not None) != (status == 'catalog_verified') or (skin is not None and (did, skin) not in known_doll_costumes()):
            raise ValueError('invalid dorm costume reference')
        flag = row['dorm_reference_match_candidate']
        if ref_state == 'catalog_roster_match':
            if flag not in ('0','1'): raise ValueError('invalid dorm reference match flag')
            if flag == '1':
                from .helix_catalog import catalog
                if did not in catalog(): raise ValueError('unknown dorm reference model')
                matched += 1
        elif flag != '': raise ValueError('withheld dorm reference must be blank')
    if matched != int(ref_state == 'catalog_roster_match') or meta['costume_reference_counts'] != counts:
        raise ValueError('dorm projection summary mismatch')
