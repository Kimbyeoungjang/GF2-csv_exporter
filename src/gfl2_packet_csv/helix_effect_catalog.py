"""Read-only static helix effect references, not account unlock projections.

Node links are raw directed references, not proven prerequisites. Node costs
and effect costs remain separate. No inferred stacking, UI scaling or bitmask
interpretation is performed.
"""
from dataclasses import dataclass
from .reference_data import lru_cache
from types import MappingProxyType
from .doll_growth_catalog import _rows
from .doll_stat_catalog import StatProperty, catalog as stat_catalog
from .helix_catalog import catalog as helix_catalog
from .key_catalog import catalog as key_catalog


@dataclass(frozen=True)
class ItemCost:
    item_id: int
    quantity: int


@dataclass(frozen=True)
class NodeEffect:
    doll_id: int
    node_id: int
    node_type_raw: int
    effect_group_id: int
    effect_id: int
    field5_raw: int | None
    property: StatProperty | None
    key_id: int | None
    key_reference_status: str
    effect_field8_raw: int | None
    node_costs: tuple[ItemCost, ...]
    effect_cost: ItemCost | None
    linked_node_ids: tuple[int, ...]
    bonus_group_id: int


@dataclass(frozen=True)
class Bonus:
    record_id: int
    threshold_raw: int
    property: StatProperty


@dataclass(frozen=True)
class EffectCatalog:
    nodes: MappingProxyType
    by_doll: MappingProxyType
    bonuses: MappingProxyType


def _put(target, key, value):
    if key in target:
        raise ValueError('duplicate static reference')
    target[key] = value


def _positive(value):
    n = int(value)
    if n <= 0:
        raise ValueError('nonpositive static reference')
    return n


def _optional(value):
    return _positive(value) if value else None


@lru_cache(maxsize=1)
def catalog():
    """Fail closed on missing, duplicate or inconsistent cross references."""
    try:
        groups = helix_catalog(); keys = key_catalog(); stats = stat_catalog()
        if not groups or not keys or stats is None:
            raise ValueError('missing prerequisite catalog')
        owners = {n.node_record_id: d for d,g in groups.items() for n in g.nodes}
        props, links, costs, nodes, bonuses = {}, {}, {}, {}, {}
        for r in _rows('helix_property_reference.csv'):
            pid, code, val = (_positive(r[k]) for k in ('property_id','field_code','value_units'))
            if code == 1 or code not in stats.fields or val >= 2**31:
                raise ValueError('invalid property')
            _put(props.setdefault(pid, {}), code, val)
        if not props:
            raise ValueError('incomplete property values')
        props = {pid:StatProperty(pid,tuple(sorted(vals.items()))) for pid,vals in props.items()}
        for r in _rows('helix_node_links.csv'):
            a,b = (_positive(r[k]) for k in ('node_id','linked_node_id'))
            if a==b or owners[a]!=owners[b]:
                raise ValueError('invalid node link')
            _put(links.setdefault(a,{}), b, None)
        for r in _rows('helix_node_cost_reference.csv'):
            nid,pos,item,qty = (_positive(r[k]) for k in ('node_id','position','item_id','quantity'))
            if nid not in owners or pos not in (1,2):
                raise ValueError('invalid node cost')
            _put(costs.setdefault(nid,{}), pos, ItemCost(item,qty))
        used, effect_ids, unresolved = set(), set(), set()
        for r in _rows('helix_effect_reference.csv'):
            nid,typ,eg,eid,bg = (_positive(r[k]) for k in ('node_id','node_type_raw','effect_group_id','effect_id','bonus_group_id'))
            doll=owners[nid]; p,k,f8=(_optional(r[n]) for n in ('property_id','key_id','effect_field8_raw'))
            ci,cq=(_optional(r[n]) for n in ('effect_cost_item_id','effect_cost_quantity'))
            field5=_optional(r['field5_raw'])
            if eid != eg*100+1 or eid in effect_ids or bg != 1001 or typ not in (1,3,4,6) or (ci is None)!=(cq is None):
                raise ValueError('invalid effect reference')
            effect_ids.add(eid)
            node_costs=costs.get(nid,{})
            if set(node_costs)!= (set() if typ==4 else {1,2}):
                raise ValueError('incomplete node costs')
            if typ==1:
                if p is None or k is not None or f8!=100 or ci is not None:
                    raise ValueError('invalid property effect')
                used.add(p); prop=props[p]; status='not_applicable'
            else:
                if p is not None or k is None or f8 is not None or (ci,cq)!=((9004,1) if typ==3 else (None,None)):
                    raise ValueError('invalid key effect')
                prop=None; entry=keys.get(k)
                if entry is None:
                    if doll!=1085 or k not in (208501,208502,208503,208504,208505,208506,901085):
                        raise ValueError('unexpected missing key reference')
                    unresolved.add(k); status='missing_from_key_catalog'
                else:
                    if entry[0]!={3:1,4:2,6:3}[typ] or (typ!=4 and entry[1]!=doll):
                        raise ValueError('inconsistent key reference')
                    status='catalog_consistent'
            _put(nodes,nid,NodeEffect(doll,nid,typ,eg,eid,field5,prop,k,status,f8,tuple(node_costs[i] for i in sorted(node_costs)),ItemCost(ci,cq) if ci is not None else None,tuple(sorted(links.get(nid,{}))),bg))
        if set(nodes)!=set(owners):
            raise ValueError('incomplete helix reference')
        for r in _rows('helix_bonus_reference.csv'):
            rid,bg,threshold,pid=(_positive(r[k]) for k in ('record_id','bonus_group_id','threshold_raw','property_id'))
            if bg!=1001 or threshold!=rid*2 or pid in used:
                raise ValueError('invalid bonus reference')
            used.add(pid); _put(bonuses.setdefault(bg,{}),threshold,Bonus(rid,threshold,props[pid]))
        if set(bonuses)!={1001} or set(bonuses[1001])!={2,4,6} or used!=set(props):
            raise ValueError('incomplete bonus reference')
        by_doll={d:tuple(nodes[n.node_record_id] for n in g.nodes) for d,g in groups.items()}
        return EffectCatalog(MappingProxyType(nodes),MappingProxyType(by_doll),MappingProxyType({g:tuple(b[k] for k in sorted(b)) for g,b in bonuses.items()}))
    except (OSError,ValueError,KeyError,TypeError):
        return None


def node_reference(node_id):
    c=catalog()
    return c.nodes.get(node_id) if c is not None and type(node_id) is int else None


def doll_references(doll_id):
    """Ordered by static node position, not observed unlock order."""
    c=catalog()
    return c.by_doll.get(doll_id) if c is not None and type(doll_id) is int else None


def bonus_references(doll_id):
    """All threshold records; does not choose a tier or sum bonuses."""
    refs=doll_references(doll_id)
    return catalog().bonuses.get(refs[0].bonus_group_id) if refs else None


@lru_cache(maxsize=1)
def reset_catalog():
    """Static TalentEffectResetData ID lookup, not player reset count."""
    try:
        result={}
        for r in _rows('common_key_reset_reference.csv'):
            rid,item,qty=(_positive(r[k]) for k in ('record_id','item_id','quantity'))
            if item!=9005 or qty!=rid:
                raise ValueError('invalid reset cost reference')
            _put(result,rid,ItemCost(item,qty))
        if set(result)!=set(range(1,11)):
            raise ValueError('incomplete reset cost reference')
        return MappingProxyType(result)
    except (OSError,ValueError,KeyError,TypeError):
        return MappingProxyType({})


def reset_reference(record_id):
    return reset_catalog().get(record_id) if type(record_id) is int else None
