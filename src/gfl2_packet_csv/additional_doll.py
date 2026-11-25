"""Verified projection of 11101.3.2, independent of owned dolls.
The wrapper's purpose is unconfirmed; never infer ownership or emit unknown fields.
"""
from dataclasses import dataclass
from .account_extension import parse, msg, ident, scalar, nums
from .doll_growth_catalog import class_projection, rank_projection, core_model_status
from .helix_catalog import catalog, group_projection
from .dorm_costumes import known_doll_costumes
from . import additional_doll_details as details_module
from . import doll_promotion

FILE = 'additional_doll_observations.csv'
HEADERS = 'doll_id level rank class_stage costume_id core_level core_model_id helix_mask_candidate weapon_uid weapon_reference_status'.split() + list(doll_promotion.FIELDS) + list(doll_promotion.ACTIVE_FIELDS)


def decode(data):
    d = parse(data)
    if 3 not in d:
        return None
    wrapper = msg(d, 3)
    row = msg(wrapper, 2)
    did = ident(row, 1)
    if ident(wrapper, 1) != did:
        raise ValueError('additional doll wrapper mismatch')
    if did not in catalog():
        raise ValueError('additional doll not in static catalog')
    level, rank, stage = (scalar(row, n) for n in (3, 6, 31))
    core = msg(row, 55)
    mask = nums(row, 51)
    if len(mask) > 1 or (51 in row and not mask):
        raise ValueError('unexpected additional doll mask structure')
    result = dict(doll_id=did, level=level, rank=rank, class_stage=stage,
                  costume_id=scalar(row, 5), core_level=scalar(core, 1), core_model_id=scalar(core, 3),
                  helix_mask_candidate=mask[0] if mask else None, weapon_uid=scalar(row, 62))
    validate_growth(result)
    result.update(zip(doll_promotion.FIELDS, doll_promotion.decode_pair(d, did)))
    return result


def validate_growth(row):
    did = row['doll_id']
    if did not in catalog():
        raise ValueError('unknown additional doll model')
    level, rank, stage = (row[k] for k in ('level','rank','class_stage'))
    if level is not None and not 1 <= level <= 60:
        raise ValueError('invalid additional doll level')
    if rank is not None and rank_projection(did, rank)[1] != 'catalog_consistent':
        raise ValueError('invalid additional doll rank')
    if stage is not None and class_projection(stage, level)[1] != 'catalog_consistent':
        raise ValueError('invalid additional doll class stage')
    costume = row['costume_id']
    if costume is not None and costume != 0 and (did, costume) not in known_doll_costumes():
        raise ValueError('invalid additional doll costume reference')
    model, core_level = row['core_model_id'], row['core_level']
    if model is not None and core_model_status(did, {'model_id':model}) != 'catalog_consistent':
        raise ValueError('invalid additional doll core model')
    if core_level is not None and not 0 <= core_level < (1 << 64):
        raise ValueError('invalid additional doll core level')
    mask = row['helix_mask_candidate']
    if mask is not None and group_projection(did, mask)[2] != 'within_static_node_range':
        raise ValueError('invalid additional doll mask')


def weapon_status(row, weapons, owned_claims, available):
    uid, did = row['weapon_uid'], row['doll_id']
    if uid is None: return 'field_absent'
    if uid == 0: return 'explicit_zero'
    if not available: return 'snapshot_unavailable'
    if uid not in weapons: return 'unmatched'
    return ('verified' if weapons[uid].get('equipped_doll_id') == did and
            not owned_claims.get(uid) else 'reference_mismatch')


@dataclass
class AdditionalDollObservation:
    row: dict | None = None
    status: str = 'missing'
    details: tuple | None = None
    details_status: str = 'not_observed'

    def observe(self, data):
        if self.status in ('invalid', 'conflict'): return
        try:
            row = decode(data)
        except (ValueError, TypeError, KeyError, IndexError, OverflowError):
            self.row, self.status = None, 'invalid'
            return
        if row is None:
            if self.status == 'missing': self.status = 'not_observed'
            return
        if self.row is not None and row != self.row:
            self.row, self.status = None, 'conflict'
            return
        self.row, self.status = row, 'observed'
        if self.details_status in ('invalid','conflict'): return
        try:
            details = details_module.decode(data)
        except (ValueError, TypeError, KeyError, IndexError, OverflowError):
            self.details, self.details_status = None, 'invalid'
            return
        if details is None: return
        if self.details is not None and self.details != details:
            self.details, self.details_status = None, 'conflict'
            return
        self.details, self.details_status = details, 'observed'

    def detail_projection(self, rows, common, available):
        if not rows: return {}, {'status':'parent_unavailable','projection':None}
        if self.details_status != 'observed': return {}, {'status':self.details_status,'projection':None}
        try:
            out, meta = details_module.project(rows[0]['doll_id'], self.details, common, available)
        except (ValueError, TypeError, KeyError, IndexError, OverflowError):
            return {}, {'status':'reference_unverified','projection':None}
        return out, {'status':'observed','projection':meta}

    def projection(self, *, dolls, weapons, owned_claims, doll_available, weapon_available):
        state = self.status
        rows = []
        if state == 'observed':
            if not doll_available: state = 'doll_snapshot_unavailable'
            elif self.row['doll_id'] in dolls: state = 'overlaps_owned_doll'
            else:
                row = {**self.row, 'weapon_reference_status':weapon_status(self.row, weapons, owned_claims, weapon_available)}
                row.update(zip(doll_promotion.FIELDS, doll_promotion.project(*(row[k] for k in doll_promotion.FIELDS), row['doll_id'], dolls)))
                row.update(zip(doll_promotion.ACTIVE_FIELDS, doll_promotion.active_projection(row, dolls)))
                rows = [row]
        return rows, {'status':state, 'file':FILE if rows else None,'rows':len(rows),
                      'semantic_status':'separate_record_promotion_link_candidate_not_active_form_proof'}
