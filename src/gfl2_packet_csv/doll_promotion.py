"""Static GunTransformData pairs; no inference of active form or ownership."""
from .reference_data import lru_cache
from types import MappingProxyType
from .doll_growth_catalog import _rows
from .account_extension import msg, ident

FIELDS = ('promotion_base_doll_id', 'promotion_promoted_doll_id', 'promotion_reference_status')

@lru_cache(maxsize=1)
def catalog():
    try:
        rows = _rows('doll_promotion_pairs.csv')
        pairs = {int(r['base_doll_id']): int(r['promoted_doll_id']) for r in rows}
        if (not pairs or len(pairs) != len(rows) or len(set(pairs.values())) != len(pairs)
            or set(pairs) & set(pairs.values()) or any(min(a,b)<=0 for a,b in pairs.items())):
            return MappingProxyType({})
        return MappingProxyType(pairs)
    except (ValueError, KeyError, TypeError):
        return MappingProxyType({})


def decode_pair(data, did):
    if 4 not in data:
        return None, None, 'field_absent'
    try:
        record = msg(data, 4)
        base, promoted = ident(record, 1), ident(record, 2)
    except (ValueError, KeyError, TypeError):
        return None, None, 'invalid_field'
    pairs = catalog()
    if not pairs:
        return None, None, 'catalog_unavailable'
    if pairs.get(base) != promoted:
        return None, None, 'unrecognized_pair'
    if did not in (base, promoted):
        return None, None, 'unrelated_pair'
    return base, promoted, 'catalog_pair_candidate'


def project(base, promoted, status, did, owned):
    if status == 'catalog_pair_candidate':
        if catalog().get(base) != promoted or did not in (base, promoted):
            raise ValueError('invalid promotion pair')
        other = promoted if did == base else base
        return base, promoted, 'catalog_pair_linked' if other in owned else 'counterpart_not_observed'
    return base, promoted, status


def validate(row, owned):
    base, promoted, state = (row[k] for k in FIELDS)
    if state in ('catalog_pair_linked', 'counterpart_not_observed'):
        if (base, promoted, state) != project(base, promoted, 'catalog_pair_candidate', row['doll_id'], owned):
            raise ValueError('promotion reference mismatch')
    elif state not in {'field_absent','invalid_field','catalog_unavailable','unrecognized_pair','unrelated_pair'} or base is not None or promoted is not None:
        raise ValueError('invalid withheld promotion reference')


ACTIVE_FIELDS = ('promotion_active_doll_id_candidate', 'promotion_active_reference_status')

def active_projection(row, owned):
    """Presence -> promoted, absence -> base candidate only for linked forms.

    Controlled evidence is for Nemesis; do not assume missing data implies
    base form without the separate promoted record and its owned counterpart.
    """
    did = row['doll_id']
    candidates = [base for base, promoted in catalog().items() if promoted == did and base in owned]
    if len(candidates) != 1:
        return None, 'counterpart_unavailable'
    base = candidates[0]
    state = row['promotion_reference_status']
    if state == 'catalog_pair_linked' and (row['promotion_base_doll_id'], row['promotion_promoted_doll_id']) == (base, did):
        return did, 'promoted_form_candidate_field_present'
    if state == 'field_absent':
        return base, 'base_form_candidate_field_absent'
    return None, 'pair_unverified'
