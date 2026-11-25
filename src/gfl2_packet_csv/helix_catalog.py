"""Static helix group and node references; not a proof of unlocked nodes."""
from dataclasses import dataclass
from .reference_data import lru_cache
from types import MappingProxyType
from .doll_growth_catalog import _rows


@dataclass(frozen=True)
class HelixNode:
    bit_index_candidate: int
    tree_record_id: int
    node_record_id: int


@dataclass(frozen=True)
class HelixGroup:
    group_id: int
    nodes: tuple[HelixNode, ...]


@lru_cache(maxsize=1)
def catalog():
    """Reject inconsistent projections, never guess a group from doll ID."""
    try:
        groups, owners, nodes, trees = {}, {}, set(), {}
        for row in _rows('doll_helix_nodes.csv'):
            doll, group, tree, bit, node = (int(row[k]) for k in (
                'doll_id', 'group_id', 'tree_record_id', 'bit_index_candidate', 'node_record_id'))
            if min(doll, group, tree, node) <= 0 or not 0 <= bit < 64 or node in nodes:
                return MappingProxyType({})
            if owners.setdefault(group, doll) != doll or trees.setdefault(tree, group) != group:
                return MappingProxyType({})
            if node // 1000 != tree or node // 1000000 != group:
                return MappingProxyType({})
            gid, indexed = groups.setdefault(doll, (group, {}))
            if gid != group or bit in indexed:
                return MappingProxyType({})
            nodes.add(node)
            indexed[bit] = HelixNode(bit, tree, node)
        result = {}
        for doll, (gid, indexed) in groups.items():
            if len(indexed) not in (13, 14) or set(indexed) != set(range(len(indexed))):
                return MappingProxyType({})
            result[doll] = HelixGroup(gid, tuple(indexed[i] for i in range(len(indexed))))
        return MappingProxyType(result)
    except (KeyError, TypeError, ValueError):
        return MappingProxyType({})


def group_projection(doll_id, value):
    """Range check only; all bits set is not an independently verified completion."""
    if type(value) is not int or not 0 <= value < (1 << 64):
        raise ValueError('invalid helix mask candidate')
    groups = catalog()
    if not groups:
        return None, None, 'catalog_unavailable'
    group = groups.get(doll_id)
    if group is None:
        return None, None, 'unknown_doll'
    count = len(group.nodes)
    return group.group_id, count, 'within_static_node_range' if value >> count == 0 else 'outside_static_node_range'
