"""Direct static stat references; no inferred account state or final stat formula.

PropertyData field numbers and code names are independent of affix IDs.
LevelPropertyData contains raw definitions past the currently playable cap.
"""
from dataclasses import dataclass
from decimal import Decimal
from .reference_data import lru_cache
from types import MappingProxyType
from .doll_growth_catalog import _rows, core_models, rank_records, class_caps


@dataclass(frozen=True)
class StatProperty:
    property_id: int
    values: tuple[tuple[int, int], ...]

    def value(self, field_code):
        """Absent is None, including the explicitly empty class-one property."""
        return dict(self.values).get(field_code) if type(field_code) is int else None


@dataclass(frozen=True)
class LevelStat:
    record_id: int
    property: StatProperty


@dataclass(frozen=True)
class ClassStat:
    record_id: int
    level_cap: int
    property: StatProperty


@dataclass(frozen=True)
class RankStat:
    record_id: int
    field8_raw: int | None
    field9_raw: Decimal | None


@dataclass(frozen=True)
class DollStatModel:
    base: StatProperty
    level_group_id: int
    class_group_id: int


@dataclass(frozen=True)
class StatCatalog:
    fields: MappingProxyType
    models: MappingProxyType
    levels: MappingProxyType
    classes: MappingProxyType
    ranks: MappingProxyType


@lru_cache(maxsize=1)
def catalog():
    """Fail closed on incomplete or inconsistent static data."""
    def put(target, key, value):
        if key in target:
            raise ValueError('duplicate reference')
        target[key] = value

    try:
        names, props, models, levels, classes, ranks = {}, {}, {}, {}, {}, {}
        for r in _rows('property_field_names.csv'):
            code, name = int(r['field_code']), r['code_name']
            if not name or not name.isidentifier():
                raise ValueError('invalid field name')
            put(names, code, name)
        if set(names) != set(range(1,112)) or names[1] != 'Id' or len(set(names.values())) != len(names):
            raise ValueError('incomplete property field catalog')
        for r in _rows('doll_stat_property_reference.csv'):
            pid = int(r['property_id'])
            if pid <= 0:
                raise ValueError('invalid property ID')
            if not r['field_code'] and not r['value_units']:
                put(props, pid, {})
            else:
                code, value = int(r['field_code']), int(r['value_units'])
                if code not in names or code == 1 or not 0 <= value < 2**31 or (pid in props and not props[pid]):
                    raise ValueError('invalid property value')
                put(props.setdefault(pid, {}), code, value)
        props = {pid: StatProperty(pid, tuple(sorted(values.items()))) for pid,values in props.items()}
        if {pid for pid,p in props.items() if not p.values} != {99999999}:
            raise ValueError('invalid empty property')
        for r in _rows('doll_stat_model_reference.csv'):
            did, pid, group, cls = (int(r[k]) for k in ('doll_id','base_property_id','level_group_id','class_group_id'))
            if min(did,pid,group,cls) <= 0 or not props[pid].values:
                raise ValueError('invalid model')
            put(models,did,DollStatModel(props[pid],group,cls))
        if set(models) != set(core_models()) or len({m.class_group_id for m in models.values()}) != len(models) or len({m.base.property_id for m in models.values()}) != len(models):
            raise ValueError('incomplete model join')
        for r in _rows('doll_stat_level_reference.csv'):
            group, level, rid, pid = (int(r[k]) for k in ('group_id','level','level_record_id','property_id'))
            if min(group,level,rid,pid) <= 0 or rid != group*1000+level or rid != pid or not props[pid].values:
                raise ValueError('invalid level reference')
            put(levels,(group,level),LevelStat(rid,props[pid]))
        if set(levels) != {(m.level_group_id,l) for m in models.values() for l in range(1,81)}:
            raise ValueError('incomplete level reference')
        for r in _rows('doll_stat_class_reference.csv'):
            group, stage, rid, cap, pid = (int(r[k]) for k in ('class_group_id','stage','class_record_id','level_cap','property_id'))
            if min(group,stage,rid,cap,pid)<=0 or class_caps().get(stage)!=cap or (stage==1)!=(not props[pid].values):
                raise ValueError('invalid class reference')
            put(classes,(group,stage),ClassStat(rid,cap,props[pid]))
        groups={group for group,_ in classes}
        if not {m.class_group_id for m in models.values()} <= groups or set(classes)!={(group,stage) for group in groups for stage in range(1,6)} or len({c.record_id for c in classes.values()})!=len(classes):
            raise ValueError('incomplete class reference')
        for r in _rows('doll_stat_rank_reference.csv'):
            did,rank,rid=(int(r[k]) for k in ('doll_id','rank','grade_record_id'))
            a=int(r['field8_raw']) if r['field8_raw'] else None
            b=Decimal(r['field9_raw']) if r['field9_raw'] else None
            if rank_records().get((did,rank))!=rid or (rank==0 and (a is not None or b is not None)) or (rank>0 and (a!=30 or b!=Decimal(rank)/20)):
                raise ValueError('invalid rank reference')
            put(ranks,(did,rank),RankStat(rid,a,b))
        if set(ranks)!=set(rank_records()) or not ranks:
            raise ValueError('incomplete rank reference')
        used={m.base.property_id for m in models.values()}|{r.property.property_id for r in levels.values()}|{r.property.property_id for r in classes.values()}
        if used != set(props):
            raise ValueError('unreferenced property')
        return StatCatalog(*(MappingProxyType(x) for x in (names,models,levels,classes,ranks)))
    except (OSError, ValueError, TypeError, KeyError, ArithmeticError):
        return None


def field_name(field_code):
    c=catalog()
    return c.fields.get(field_code) if c is not None and type(field_code) is int else None


def model_reference(doll_id):
    c=catalog()
    return c.models.get(doll_id) if c is not None and type(doll_id) is int else None


def level_reference(doll_id,level):
    """Static definition only; 61–80 are not valid current account levels."""
    m=model_reference(doll_id)
    return catalog().levels.get((m.level_group_id,level)) if m is not None and type(level) is int else None


def class_reference(doll_id,stage):
    m=model_reference(doll_id)
    return catalog().classes.get((m.class_group_id,stage)) if m is not None and type(stage) is int else None


def rank_reference(doll_id,rank):
    c=catalog()
    return c.ranks.get((doll_id,rank)) if c is not None and type(doll_id) is int and type(rank) is int else None
