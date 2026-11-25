"""Static counter-rule and challenge joins, without interpreting completion or flags."""
from dataclasses import dataclass
from .reference_data import lru_cache
from types import MappingProxyType
from .reference_data import files
import csv
from .doll_growth_catalog import _rows
from .task_reference_catalog import _uint, task_references, challenge_explicit_links
from .achievement_catalog import challenge_ids


@dataclass(frozen=True)
class CounterRule:
    rule_id: int
    rule_name: str | None
    rule_proto_name: str | None
    flags_raw: tuple[int | None, ...]


@dataclass(frozen=True)
class ConditionReference:
    condition_id: int
    rule_id: int
    target_raw: int
    parameters_raw: tuple[int | None, ...]


@dataclass(frozen=True)
class ChallengeReference:
    challenge_id: int
    field2_raw: int
    field5_raw: int | None
    field10_raw: int | None
    field12_raw: int | None
    same_id_condition: ConditionReference | None
    explicit_conditions: tuple[ConditionReference, ...]


def _empty():
    return MappingProxyType({})


@lru_cache(maxsize=1)
def counter_rules():
    try:
        result={}
        for r in _rows('counter_rule_reference.csv'):
            i=_uint(r['rule_id']); name=r['rule_name'] or None; proto=r['rule_proto_name'] or None
            flags=tuple(_uint(r[f'field{f}_raw'],True) for f in range(2,7))
            if not i or i in result or bool(name)!=bool(proto) or any(v not in (None,1,2) for v in flags): raise ValueError('invalid rule')
            result[i]=CounterRule(i,name,proto,flags)
        tasks=task_references()
        if not result or not tasks or any(t.rule_id not in result for t in tasks.values()): raise ValueError('incomplete rules')
        return MappingProxyType(result)
    except (KeyError,ValueError,TypeError): return _empty()


@lru_cache(maxsize=1)
def condition_references():
    try:
        rules=counter_rules(); result={}
        with files('gfl2_packet_csv.static_achievements').joinpath('achievement_conditions_ko.csv').open('r',encoding='utf-8-sig',newline='') as f:
            for r in csv.DictReader(f):
                i=_uint(r['condition_id']); rule=_uint(r['rule_type_id'])
                if not i or i in result or rule not in rules or (r['rule_name'],r['rule_proto_name'])!=(rules[rule].rule_name,rules[rule].rule_proto_name): raise ValueError('invalid condition')
                result[i]=ConditionReference(i,rule,_uint(r['target_raw_field_3']),tuple(_uint(r[f'parameter_raw_field_{j}'],True) for j in range(4,10)))
        if not result: raise ValueError('missing conditions')
        return MappingProxyType(result)
    except (OSError,KeyError,ValueError,TypeError): return _empty()


@lru_cache(maxsize=1)
def challenge_references():
    try:
        conditions=condition_references(); links=challenge_explicit_links(); ids=challenge_ids(); result={}
        if not conditions or not links or not ids: raise ValueError('missing references')
        with files('gfl2_packet_csv.static_achievements').joinpath('achievement_challenge_ko.csv').open('r',encoding='utf-8-sig',newline='') as f:
            old={_uint(r['challenge_id']):(_uint(r['field_2_raw']),_uint(r['field_5_raw'],True)) for r in csv.DictReader(f)}
        if set(old)!=set(ids): raise ValueError('incomplete challenge definitions')
        for r in _rows('challenge_condition_reference.csv'):
            i=_uint(r['challenge_id']); f2=_uint(r['field2_raw']); f5,f10,f12,same=(_uint(r[k],True) for k in ('field5_raw','field10_raw','field12_raw','same_id_condition'))
            if i not in ids or i in result or old[i]!=(f2,f5) or not f2 or (f5 is not None and (f5 not in ids or f5==i)) or same!=(i if i in conditions else None): raise ValueError('invalid challenge')
            explicit=tuple(conditions[d] for d in links.get(i,()))
            if same is None and not explicit: raise ValueError('missing condition reference')
            result[i]=ChallengeReference(i,f2,f5,f10,f12,conditions.get(same),explicit)
        if set(result)!=set(ids): raise ValueError('incomplete challenges')
        for i in result:
            seen=set(); cursor=i
            while cursor is not None:
                if cursor in seen: raise ValueError('cyclic field5 references')
                seen.add(cursor); cursor=result[cursor].field5_raw
        return MappingProxyType(result)
    except (OSError,KeyError,ValueError,TypeError): return _empty()


def counter_rule(rule_id):
    return counter_rules().get(rule_id)


def condition_reference(condition_id):
    return condition_references().get(condition_id)


def challenge_reference(challenge_id):
    return challenge_references().get(challenge_id)


def task_rule(task_id):
    task=task_references().get(task_id)
    return counter_rule(task.rule_id) if task is not None else None
