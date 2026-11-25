"""Costume reference projections. State codes are observations, not UI semantics."""
from .reference_data import lru_cache
from .dorm_costumes import known_doll_costumes

@lru_cache(maxsize=1)
def costume_owners():
    owners = {}
    for doll, costume in known_doll_costumes():
        owners.setdefault(costume, set()).add(doll)
    return {k: frozenset(v) for k, v in owners.items()}


def current_projection(doll_id, costume_id, state, wardrobe):
    catalog = costume_owners()
    if costume_id is None: ref = 'field_absent'
    elif costume_id == 0: ref = 'explicit_zero'
    elif not catalog: ref = 'catalog_unavailable'
    elif costume_id not in catalog: ref = 'unknown_costume'
    elif doll_id not in catalog[costume_id]: ref = 'doll_model_mismatch'
    else: ref = 'catalog_consistent'
    if costume_id is None or costume_id == 0: relation = 'costume_unavailable'
    elif str(costume_id) not in wardrobe: relation = 'not_in_wardrobe_snapshot'
    else:
        other = wardrobe[str(costume_id)]['state_raw']
        if state is None or other is None: relation = 'state_unavailable'
        else: relation = 'state_equal' if state == other else 'state_different'
    return ref, relation


def wardrobe_projection(costume_id, state, peer):
    catalog = costume_owners()
    owners = catalog.get(costume_id, ())
    if not catalog: ref = 'catalog_unavailable'
    elif not owners: ref = 'unknown_costume'
    elif len(owners) != 1: ref = 'ambiguous_doll'
    else: ref = 'catalog_consistent'
    owner = next(iter(owners)) if len(owners) == 1 else None
    if peer is None: relation = 'peer_snapshot_unavailable'
    elif str(costume_id) not in peer: relation = 'not_in_peer_snapshot'
    else:
        other = peer[str(costume_id)]['state_raw']
        if state is None or other is None: relation = 'peer_state_unavailable'
        else: relation = 'peer_state_equal' if state == other else 'peer_state_different'
    return owner, ref, relation


PART_COLUMNS = ('part_index_low_candidate', 'part_index_middle_candidate',
                'part_index_high_candidate', 'part_index_fourth_candidate',
                'upper_part_index_candidate', 'lower_part_index_candidate',
                'leg_part_index_candidate', 'part_projection_status')

def part_projection(costume_id, state):
    """Observed 3/4-nibble wardrobe encoding. Positions count from LSB.

    Semantic upper/lower labels are gated to the two controlled costume
    models, not extrapolated to every costume or the optional fourth part.
    """
    empty = (None,) * 7
    if state is None:
        return (*empty, 'field_absent')
    if costume_id not in costume_owners():
        return (*empty, 'unknown_costume')
    if type(state) is not int or not 0x100 <= state <= 0xffff:
        return (*empty, 'unrecognized_pattern')
    n = 4 if state >= 0x1000 else 3
    parts = tuple((state >> (4 * i)) & 15 for i in range(n))
    if any(p not in (1, 2, 3) for p in parts):
        return (*empty, 'unrecognized_pattern')
    low, mid, high = parts[:3]
    fourth = parts[3] if n == 4 else None
    # Nikita's upper 1 -> 3 and Sakura's lower 1 -> 2, respectively.
    upper = low if costume_id == 1105502 and n == 3 else None
    lower = (mid if costume_id == 1105502 else high if costume_id == 1106801 else None) if n == 3 else None
    legs = high if costume_id in (1105502,1105203) and n == 3 else None
    status = 'controlled_model_partial' if any(v is not None for v in (upper,lower,legs)) else 'nibble_structure_candidate'
    return low, mid, high, fourth, upper, lower, legs, status
