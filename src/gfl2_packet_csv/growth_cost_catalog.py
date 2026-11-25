"""Static growth-cost and recovery references, independent of account packets.

The two cumulative sequences are verified by differences against the original
rows. The first is also verified against WeaponRecoverData item 8301. The
second resource's identity and runtime modifiers are not inferred. Level 0
is a source-table sentinel, not a valid owned-weapon level.
"""
from dataclasses import dataclass
from .reference_data import lru_cache
from types import MappingProxyType
from .doll_growth_catalog import _rows
from .weapon_growth_catalog import limits
from .weapon_property_catalog import level_reference


@dataclass(frozen=True)
class WeaponModel:
    grade_code: int
    field14_raw: int
    field15_raw: int
    field16_raw: int


@dataclass(frozen=True)
class WeaponCost:
    record_id: int
    level_field2: int | None
    level_cap: int
    field5_raw: int | None
    field6_raw: int | None
    field7_raw: int | None
    field8_raw: int | None
    field9_id: int
    field10_item_id: int


@dataclass(frozen=True)
class Recovery:
    item_id: int
    quantity: int


@dataclass(frozen=True)
class CostDelta:
    resource_item_id: int
    resource_quantity: int
    second_resource_raw: int


@dataclass(frozen=True)
class DollLevelAux:
    gun_level_exp_field2_raw: int | None
    gun_level_exp_field4_id: int
    level_up_field2_raw: int | None
    level_up_field6_raw: int | None


@dataclass(frozen=True)
class Catalog:
    models: MappingProxyType
    costs: MappingProxyType
    recovery: MappingProxyType
    star_recovery: MappingProxyType
    doll_levels: MappingProxyType


def _optional(row, key):
    return int(row[key]) if row[key] != '' else None


@lru_cache(maxsize=1)
def catalog():
    def put(target, key, value):
        if key in target:
            raise ValueError('duplicate reference')
        target[key] = value

    try:
        models, costs, recovery, stars, dolls = {}, {}, {}, {}, {}
        bounds = limits()
        for r in _rows('weapon_exp_model_reference.csv'):
            m,g,a,b,c = (int(r[k]) for k in ('model_id','grade_code','field14_raw','field15_raw','field16_raw'))
            if g not in (3,4,5) or a != 1000 or b != {3:400,4:1000,5:2000}[g] or c != b*3 or bounds.get(m)!=(60,6):
                raise ValueError('invalid model reference')
            put(models,m,WeaponModel(g,a,b,c))
        if not models or set(models)!=set(bounds):
            raise ValueError('missing model')
        for r in _rows('weapon_exp_reference.csv'):
            rid,g,cap,ref,item = (int(r[k]) for k in ('record_id','grade_code','level_cap','field9_id','field10_item_id'))
            raw_level = _optional(r,'level_field2')
            l = 0 if raw_level is None else raw_level
            values = tuple(_optional(r,f'field{i}_raw') for i in range(5,9))
            if (g not in (3,4,5) or not 0<=l<=60 or (l==0 and raw_level is not None) or rid!=l*100+g or cap!=60 or ref!=30000+max(l,1) or item!=8301 or
                (l<2 and any(v is not None for v in values)) or (l>=2 and any(v is None or v<=0 for v in values))):
                raise ValueError('invalid cost reference')
            put(costs,(g,l),WeaponCost(rid,raw_level,cap,*values,ref,item))
        if set(costs)!={(g,l) for g in (3,4,5) for l in range(61)}:
            raise ValueError('incomplete cost reference')
        for l in range(61):
            base=costs[5,l]
            for g in (3,4,5):
                x=costs[g,l]
                if (x.field5_raw,x.field6_raw,x.field7_raw,x.field8_raw)!=(base.field5_raw,base.field6_raw,base.field7_raw,base.field8_raw):
                    raise ValueError('grade cost mismatch')
                if l>=2:
                    p=costs[g,l-1]
                    if x.field7_raw-(p.field7_raw or 0)!=x.field5_raw or x.field8_raw-(p.field8_raw or 0)!=x.field6_raw:
                        raise ValueError('cumulative mismatch')
        for r in _rows('weapon_recover_reference.csv'):
            l,n=int(r['level']),int(r['matching_fields'])
            item,q=_optional(r,'item_id'),_optional(r,'quantity')
            if l==1:
                if (item,q,n)!=(None,None,0): raise ValueError('invalid empty recovery')
                v=None
            else:
                cost=costs[5,l]
                if (item,q,n)!=(cost.field10_item_id,cost.field7_raw,6): raise ValueError('recovery mismatch')
                v=Recovery(item,q)
            put(recovery,l,v)
        if set(recovery)!=set(range(1,61)):raise ValueError('missing recovery')
        for r in _rows('weapon_star_recover_reference.csv'):
            rid=int(r['record_id']);v=_optional(r,'field2_raw')
            if not 1<=rid<=7 or v!=(rid-1 if rid>1 else None):raise ValueError('invalid star recovery')
            put(stars,rid,v)
        if set(stars)!=set(range(1,8)):raise ValueError('missing star recovery')
        for r in _rows('doll_level_aux_reference.csv'):
            l=int(r['level']);ref=int(r['gun_level_exp_field4_id'])
            a,b,c=(_optional(r,k) for k in ('gun_level_exp_field2_raw','level_up_field2_raw','level_up_field6_raw'))
            if level_reference(l) is None or ref!=100000+l or (l==1 and any(v is not None for v in (a,b,c))) or (l>1 and any(v is None or v<=0 for v in (a,b,c))):
                raise ValueError('invalid doll level reference')
            put(dolls,l,DollLevelAux(a,ref,b,c))
        if set(dolls)!=set(range(1,61)):raise ValueError('missing doll level reference')
        return Catalog(*(MappingProxyType(x) for x in (models,costs,recovery,stars,dolls)))
    except (OSError,ValueError,TypeError,KeyError,ArithmeticError):
        return None


def weapon_model(model_id):
    c=catalog()
    return c.models.get(model_id) if c is not None and type(model_id) is int else None


def weapon_cost(model_id, level):
    """Owned-weapon levels only (1–60); sentinel is not exposed here."""
    m=weapon_model(model_id)
    return catalog().costs.get((m.grade_code,level)) if m is not None and type(level) is int and 1<=level<=60 else None


def weapon_upgrade_delta(model_id, from_level, to_level):
    """Difference between static cumulative totals, not a live payable cost.

    Partial experience, discounts and materials already invested are excluded.
    At level one the verified empty cumulative row is treated as zero only here.
    """
    if type(from_level) is not int or type(to_level) is not int or from_level>to_level:
        return None
    a,b=weapon_cost(model_id,from_level),weapon_cost(model_id,to_level)
    if a is None or b is None:return None
    return CostDelta(b.field10_item_id,(b.field7_raw or 0)-(a.field7_raw or 0),(b.field8_raw or 0)-(a.field8_raw or 0))


def weapon_recovery(level):
    """Level-only static reference. Level 1 has an explicit empty definition."""
    c=catalog()
    return c.recovery.get(level) if c is not None and type(level) is int else None


def doll_level_aux(level):
    c=catalog()
    return c.doll_levels.get(level) if c is not None and type(level) is int else None
