"""Static level conditions, helix topology, and support battle skill references.
No account unlock, combat effect, or gameplay eligibility is inferred.
"""
from dataclasses import dataclass
from .reference_data import lru_cache
from types import MappingProxyType
import csv
from .reference_data import files
from .doll_growth_catalog import _rows
from .helix_effect_catalog import catalog as effects
from .helix_catalog import catalog as helix
from .support_catalog import group_references

@dataclass(frozen=True)
class LevelCondition:
    condition_id: int
    required_level: int
    name_lang_id: int
    description_lang_id: int

@dataclass(frozen=True)
class TreeReference:
    tree_id: int
    position: int
    next_tree_id: int | None
    field5_raw: int | None

@dataclass(frozen=True)
class NodeProgression:
    node_id: int
    condition: LevelCondition | None
    incoming_node_ids: tuple[int, ...]
    outgoing_node_ids: tuple[int, ...]

@dataclass(frozen=True)
class BattleSkillReference:
    field_code: int
    position: int
    battle_skill_id: int
    display_present: bool

@dataclass(frozen=True)
class SupportStage:
    record_id: int
    group_id: int
    stage: int
    links: tuple[BattleSkillReference, ...]


def _insert(target, key, value):
    if key in target: raise ValueError('duplicate reference')
    target[key] = value


def _pos(v):
    n = int(v)
    if n <= 0: raise ValueError('nonpositive reference')
    return n


def _opt(v): return _pos(v) if v else None


def _ordered(d):
    if set(d)!=set(range(1,len(d)+1)): raise ValueError('noncontiguous positions')
    return tuple(d[i] for i in range(1,len(d)+1))


def _detail_rows():
    with files('gfl2_packet_csv.static_achievements').joinpath('achievement_detail_ko.csv').open(encoding='utf-8-sig',newline='') as f:
        return list(csv.DictReader(f))

@lru_cache(maxsize=1)
def helix_progression_catalog():
    try:
        e, h = effects(), helix()
        if e is None or not h: raise ValueError('missing helix references')
        details={}
        for row in _detail_rows(): _insert(details,_pos(row['achievement_id']),row)
        conditions={}
        for r in _rows('helix_level_conditions.csv'):
            nid,cid,level,name,desc=(_pos(r[k]) for k in ('node_id','condition_id','required_level','name_lang_id','description_lang_id'))
            a=details[cid]
            if e.nodes[nid].field5_raw!=cid or level not in (1,20,25,30,35,40,60) or (level,name,desc)!=(int(a['field_7_raw']),int(a['name_lang_id']),int(a['description_lang_id'])) or a['field_2_raw']!='1' or a['name_ko']!='뉴럴 헬릭스 특성 노드 해금 조건' or a['description_ko'] not in (f'인형 레벨 {level} 달성',f'인형 레벨 {level}로 레벨업'):
                raise ValueError('condition mismatch')
            _insert(conditions,nid,LevelCondition(cid,level,name,desc))
        if set(conditions)!={n for n,r in e.nodes.items() if r.field5_raw is not None}: raise ValueError('incomplete conditions')
        owners={n.tree_record_id:d for d,g in h.items() for n in g.nodes}
        trees={}; seen=set()
        for r in _rows('helix_tree_sequence.csv'):
            d,t,p=(_pos(r[k]) for k in ('doll_id','tree_id','position')); nxt=_opt(r['next_tree_id']); f5=_opt(r['field5_raw'])
            if t in seen or owners[t]!=d or f5 not in (None,1,2):raise ValueError('tree mismatch')
            seen.add(t); _insert(trees.setdefault(d,{}),p,TreeReference(t,p,nxt,f5))
        if seen!=set(owners) or set(trees)!=set(h):raise ValueError('incomplete trees')
        sequences={d:_ordered(ts) for d,ts in trees.items()}
        for seq in sequences.values():
            if tuple(t.next_tree_id for t in seq)!=tuple(t.tree_id for t in seq[1:])+(None,):raise ValueError('invalid tree chain')
        incoming={}
        for n,r in e.nodes.items():
            for target in r.linked_node_ids:
                _insert(incoming,target,n)
        # Check acyclicity, without interpreting edges as sufficient unlock conditions.
        for n in e.nodes:
            visited=set(); p=n
            while p in incoming:
                if p in visited: raise ValueError('cycle in node graph')
                visited.add(p); p=incoming[p]
        nodes={n:NodeProgression(n,conditions.get(n),(incoming[n],) if n in incoming else (),r.linked_node_ids) for n,r in e.nodes.items()}
        return MappingProxyType(nodes),MappingProxyType(sequences)
    except (OSError,ValueError,KeyError,TypeError):return None


def node_progression(node_id):
    c=helix_progression_catalog();return c[0].get(node_id) if c is not None and type(node_id) is int else None


def tree_sequence(doll_id):
    c=helix_progression_catalog();return c[1].get(doll_id) if c is not None and type(doll_id) is int else None

@lru_cache(maxsize=1)
def support_progression_catalog():
    try:
        units, old=group_references()
        known={_pos(r['skill_id']) for r in _rows('battle_skill_ids.csv')}
        stages={}; groups={}; links={}
        for r in _rows('support_skill_stages.csv'):
            rid,g,s=(_pos(r[k]) for k in ('record_id','group_id','stage'))
            if old[rid]!=g:raise ValueError('support group mismatch')
            _insert(stages,rid,(g,s));_insert(groups.setdefault(g,{}),s,rid)
        if set(stages)!=set(old) or not set(units.values())<=set(groups):raise ValueError('incomplete stages')
        sequences={g:_ordered(v) for g,v in groups.items()}
        if any(len(v) not in (6,10) for v in sequences.values()):raise ValueError('invalid stage count')
        for r in _rows('support_battle_skill_links.csv'):
            rid,f,p,s=(_pos(r[k]) for k in ('record_id','field_code','position','battle_skill_id')); present=r['display_present']
            if rid not in stages or f not in (5,8,18) or s not in known or present not in ('0','1'):raise ValueError('invalid skill reference')
            _insert(links.setdefault(rid,{}).setdefault(f,{}),p,BattleSkillReference(f,p,s,present=='1'))
        if set(links)!=set(stages):raise ValueError('missing skill links')
        result={}
        for rid,(g,s) in stages.items():
            by_field=links[rid]
            if len(by_field.get(5,{}))!=1 or not by_field[5][1].display_present or not by_field.get(18):raise ValueError('missing mandatory reference')
            result[rid]=SupportStage(rid,g,s,tuple(v for f in sorted(by_field) for v in _ordered(by_field[f])))
        return MappingProxyType(result),MappingProxyType({g:tuple(result[rid] for rid in seq) for g,seq in sequences.items()})
    except (OSError,ValueError,KeyError,TypeError):return None


def support_stage(record_id):
    c=support_progression_catalog();return c[0].get(record_id) if c is not None and type(record_id) is int else None


def support_group_stages(group_id):
    c=support_progression_catalog();return c[1].get(group_id) if c is not None and type(group_id) is int else None
