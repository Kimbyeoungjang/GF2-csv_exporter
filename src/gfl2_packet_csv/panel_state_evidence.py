"""Conditional evidence for absent panel inputs, never a decoded zero mask.

Level gates bound the candidate property nodes; empty reverse inventory is
only an observation, not proof that every attachment is represented.
"""
from collections import defaultdict
from .reference_data import lru_cache
from . import helix_effect_catalog as he
from .doll_growth_catalog import _rows
from .equipment_graph_audit import audit_equipment_graph


@lru_cache(maxsize=1)
def helix_level_gates():
    refs = he.catalog()
    if refs is None: raise ValueError('helix catalog unavailable')
    gates = {}
    for r in _rows('helix_level_conditions.csv'):
        nid, cid, level = (int(r[k]) for k in ('node_id','condition_id','required_level'))
        if nid in gates or nid not in refs.nodes or refs.nodes[nid].field5_raw != cid or level <= 0:
            raise ValueError('invalid helix level gate')
        gates[nid] = level
    if set(gates) != {n.node_id for n in refs.nodes.values() if n.field5_raw is not None}:
        raise ValueError('incomplete helix level gates')
    return gates


def absent_helix_evidence(did, level):
    refs = he.doll_references(did)
    if not refs or type(level) is not int or not 1 <= level <= 60:
        return None
    gates = helix_level_gates()
    eligible = [n for n in refs if gates.get(n.node_id,0) <= level]
    direct = [n for n in eligible if n.property is not None]
    fixed = sum(n.node_type_raw == 3 for n in eligible)
    bonuses = [b for b in he.bonus_references(did) if b.threshold_raw <= fixed]
    # Over-approximate: do not assume all level-eligible nodes are unlocked.
    # A field absent from every possible direct and threshold effect is invariant.
    changing = {c for n in direct for c,v in n.property.values if v}
    changing |= {c for b in bonuses for c,v in b.property.values if v}
    return {'status':'conditional_static_level_gate_not_mask_decoding',
            'eligible_direct_nodes':len(direct),'eligible_fixed_nodes':fixed,
            'possible_thresholds':[b.threshold_raw for b in bonuses],
            'possibly_nonzero_fields':sorted(changing)}


def weapon_slot_evidence(bundle, graph=None):
    if not bundle.complete_capture: raise ValueError('complete bundle required')
    g = (audit_equipment_graph(bundle) if graph is None else graph)['current_weapon_parts']
    c = g['counts']
    reliable = (g['source_ready'] and g['target_ready'] and c.get('positive_claims',0)==c.get('matching_owner',0)
                and not any(c.get(k,0) for k in ('repeated_same_owner_positions','multiple_owner_items','reverse_without_matching_claim')))
    reverse = defaultdict(int); slots = defaultdict(list); unknown_owner = False
    for r in bundle.rows['attachments.csv']:
        value = r['weapon_uid']
        if value in ('',None): unknown_owner = True
        elif int(value)>0: reverse[int(value)]+=1
    for r in bundle.rows['weapon_slot_observations.csv']:
        slots[int(r['weapon_uid'])].append(r)
    out = {}
    for r in bundle.rows['weapons.csv']:
        uid = int(r['uid']); observed=slots[uid]
        if observed:
            status='slot_rows_observed'
        elif reliable and not unknown_owner and not reverse[uid]:
            status='no_positive_reverse_owner_candidate'
        else:
            status='slot_state_unresolved'
        out[uid]={'status':status,'observed_slot_rows':len(observed),'reverse_part_count':reverse[uid],
                  'reverse_graph_consistent':bool(reliable),'all_part_owner_values_present':not unknown_owner}
    return out
