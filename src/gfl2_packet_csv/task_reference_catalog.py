"""Static task definitions and explicit challenge links; no completion inference."""
from dataclasses import dataclass
from .reference_data import lru_cache
from types import MappingProxyType
from collections import defaultdict
import csv
from .reference_data import files
from .doll_growth_catalog import _rows
from .progress_task_candidate import task_categories
from .achievement_catalog import challenge_ids


@dataclass(frozen=True)
class TaskType:
    category: int
    code: str
    lang_id: int
    name_ko: str


@dataclass(frozen=True)
class TaskReference:
    task_id: int
    category: int
    name_lang_id: int
    name_ko: str
    description_lang_id: int
    description_ko: str
    rule_id: int
    field6_raw: int
    field7_item_id: int
    field7_quantity: int
    field8_raw: str
    field9_raw: int | None
    field10_raw: int | None
    field11_raw: int | None
    season_id: int


def _uint(value, optional=False):
    if optional and value == '':return None
    if not isinstance(value,str) or not value.isascii() or not value.isdecimal():raise ValueError('invalid unsigned field')
    n=int(value)
    if n>2**64-1:raise ValueError('overflow')
    return n


@lru_cache(maxsize=1)
def task_types():
    try:
        result={}
        for r in _rows('bp_task_types.csv'):
            t=TaskType(_uint(r['category']),r['code'],_uint(r['lang_id']),r['name_ko'])
            if t.category in result or not t.code or not t.lang_id or not t.name_ko:raise ValueError('invalid type')
            result[t.category]=t
        if set(result)!={1,2,3,4,5} or len({t.code for t in result.values()})!=5:raise ValueError('invalid types')
        return MappingProxyType(result)
    except (TypeError,KeyError,ValueError):return MappingProxyType({})


@lru_cache(maxsize=1)
def task_references():
    try:
        types=task_types(); categories=task_categories(); result={}
        for r in _rows('bp_task_details.csv'):
            t=TaskReference(*(_uint(r[k]) if k in {'task_id','category','name_lang_id','description_lang_id','rule_id','field6_raw','field7_item_id','field7_quantity','season_id'} else _uint(r[k],True) if k in {'field9_raw','field10_raw','field11_raw'} else r[k] for k in TaskReference.__dataclass_fields__))
            if t.task_id in result or categories.get(t.task_id)!=t.category or t.category not in types or not all((t.task_id,t.name_lang_id,t.name_ko,t.description_lang_id,t.description_ko,t.rule_id,t.field7_item_id,t.field7_quantity,t.season_id)):
                raise ValueError('invalid task reference')
            result[t.task_id]=t
        if not categories or set(result)!=set(categories):raise ValueError('incomplete task references')
        return MappingProxyType(result)
    except (TypeError,KeyError,ValueError):return MappingProxyType({})


def task_reference(task_id):
    return task_references().get(task_id)


def tasks_for_type(category):
    return tuple(t for t in task_references().values() if t.category==category)


@lru_cache(maxsize=1)
def challenge_explicit_links():
    """Field 4 only. Absence of a list does not mean no condition exists."""
    try:
        with files('gfl2_packet_csv.static_achievements').joinpath('achievement_conditions_ko.csv').open('r',encoding='utf-8-sig',newline='') as f:
            conditions={int(r['condition_id']) for r in csv.DictReader(f)}
        challenges=challenge_ids(); grouped=defaultdict(list); seen=set()
        for r in _rows('achievement_explicit_condition_links.csv'):
            c,p,d=(_uint(r[k]) for k in ('challenge_id','position','condition_id'))
            if c not in challenges or d not in conditions or (c,d) in seen or p!=len(grouped[c])+1:raise ValueError('invalid link')
            seen.add((c,d));grouped[c].append(d)
        if not grouped:raise ValueError('missing explicit links')
        return MappingProxyType({c:tuple(ds) for c,ds in grouped.items()})
    except (OSError,TypeError,KeyError,ValueError):return MappingProxyType({})


def explicit_conditions(challenge_id):
    """None for unknown/unavailable, () for a known challenge without field 4."""
    links=challenge_explicit_links()
    if not links or challenge_id not in challenge_ids():return None
    return links.get(challenge_id,())
