"""Ordered static integer lists; raw values are not necessarily active effects."""
from dataclasses import dataclass
from .reference_data import lru_cache
from types import MappingProxyType
from collections import Counter
from .attachment_affixes import _rows


@dataclass(frozen=True)
class StaticList:
    field_present: bool
    values: tuple[int, ...]


def _load():
    def ids(name, field):
        return {int(r[field]) for r in _rows(name)}
    helix = _rows('doll_helix_nodes.csv')
    expected = {
        ('SquadTalentGunData',9):{int(r['doll_id']) for r in helix},
        ('SquadTalentTreeData',4):{int(r['tree_record_id']) for r in helix},
        ('SquadTalentTreeData',8):{int(r['tree_record_id']) for r in helix},
        ('SquadTalentGroupData',4):{int(r['node_record_id']) for r in helix},
        ('TalentBonusByTalentBonusGroupData',2):ids('helix_bonus_reference.csv','bonus_group_id'),
        ('TalentKeyCopyableCostCsData',4):ids('common_key_copy_cost_reference.csv','key_id'),
        ('SupportChipBaseChipData',9):ids('support_chip_secondary_values.csv','model_id'),
    }
    counts={}
    for r in _rows('static_list_counts.csv'):
        key=(r['table_name'],int(r['record_id']),int(r['field_number']))
        present,count=int(r['field_present']),int(r['value_count'])
        if key in counts or present not in (0,1) or not 0<=count<=10000 or (not present and count):
            raise ValueError('invalid list count')
        counts[key]=(present,count)
    if not all(expected.values()) or set(counts)!={(t,r,f) for (t,f),rs in expected.items() for r in rs}:
        raise ValueError('incomplete record coverage')
    grouped={k:{} for k in counts}
    for r in _rows('static_list_values.csv'):
        key=(r['table_name'],int(r['record_id']),int(r['field_number']))
        pos,v=int(r['position']),int(r['value_raw'])
        if key not in grouped or not 1<=pos<=counts[key][1] or not 0<=v<2**64 or pos in grouped[key]:
            raise ValueError('invalid list value')
        grouped[key][pos]=v
    out={}
    for k,(present,count) in counts.items():
        if len(grouped[k])!=count:
            raise ValueError('incomplete list')
        out[k]=StaticList(bool(present),tuple(grouped[k][p] for p in range(1,count+1)))
    def flatten(table,field):
        return Counter((k[1],v) for k,x in out.items() if k[0]==table and k[2]==field for v in x.values)
    checks=[
        ('SquadTalentGunData',9,Counter(set((int(r['doll_id']),int(r['tree_record_id'])) for r in helix))),
        ('SquadTalentTreeData',4,Counter((int(r['tree_record_id']),int(r['node_record_id'])) for r in helix)),
        ('SquadTalentTreeData',8,Counter((int(r['tree_record_id']),int(r['node_record_id'])) for r in helix)),
        ('SquadTalentGroupData',4,Counter((int(r['node_id']),int(r['linked_node_id'])) for r in _rows('helix_node_links.csv'))),
        ('TalentBonusByTalentBonusGroupData',2,Counter((int(r['bonus_group_id']),int(r['record_id'])) for r in _rows('helix_bonus_reference.csv'))),
    ]
    for t,f,wanted in checks:
        if flatten(t,f)!=wanted:
            raise ValueError('list reference mismatch')
    costs=Counter((k[1],i,v) for k,x in out.items() if k[0]=='TalentKeyCopyableCostCsData' for i,v in enumerate(x.values,1))
    if costs!=Counter(tuple(int(r[n]) for n in ('key_id','copy_number','cost_quantity')) for r in _rows('common_key_copy_cost_reference.csv')):
        raise ValueError('copy cost mismatch')
    for r in _rows('support_chip_secondary_values.csv'):
        k=int(r['model_id']); c=int(r['count']); raw=out['SupportChipBaseChipData',k,9].values
        wanted=tuple(int(v) for v in r['values_sorted'].split(';') if v)
        if c!=len(wanted) or (c and tuple(sorted(raw))!=wanted):
            raise ValueError('active chip value mismatch')
    return MappingProxyType(out)


@lru_cache(maxsize=1)
def catalog():
    try:
        return _load()
    except (OSError,ValueError,TypeError,KeyError):
        return MappingProxyType({})


def list_reference(table, record_id, field_number):
    """None = unsupported/unavailable; present=False = observed field absence."""
    if type(table) is not str or type(record_id) is not int or type(field_number) is not int:
        return None
    return catalog().get((table,record_id,field_number))
