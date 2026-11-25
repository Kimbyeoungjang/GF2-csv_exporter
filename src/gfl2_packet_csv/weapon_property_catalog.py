"""Static weapon properties and skill-level references, not a final stat formula.
Field codes belong to PropertyData, not the attachment affix namespace.
LevelUpData factors are exposed without assuming which are applied to weapons.
"""
from dataclasses import dataclass
from .reference_data import lru_cache
from types import MappingProxyType
from .attachment_affixes import _rows
from .weapon_growth_catalog import limits

@dataclass(frozen=True)
class WeaponProperty:
    property_id: int
    field_code: int
    value_units: int

@dataclass(frozen=True)
class BreakReference:
    skill_id: int
    display_level: int | None

@lru_cache(maxsize=1)
def base_catalog():
    try:
        out={}
        for r in _rows('weapon_base_property_reference.csv'):
            m,p,f,v=(int(r[k]) for k in ('model_id','property_id','field_code','value_units'))
            if m not in limits() or min(p,f,v)<=0 or f==1 or any(x.field_code==f or x.property_id!=p for x in out.get(m,())):
                raise ValueError('invalid property reference')
            out.setdefault(m,[]).append(WeaponProperty(p,f,v))
        if not out or set(out)!=set(limits()): raise ValueError('incomplete model reference')
        return MappingProxyType({m:tuple(v) for m,v in out.items()})
    except (OSError,ValueError,TypeError,KeyError):
        return MappingProxyType({})

@lru_cache(maxsize=1)
def level_catalog():
    try:
        out={}
        for r in _rows('level_up_reference.csv'):
            level=int(r['level']); vals=tuple(int(r[f'field{i}_units']) for i in (3,4,5))
            if level in out or min(vals)<=0: raise ValueError('invalid level')
            out[level]=vals
        if set(out)!=set(range(1,61)):raise ValueError('incomplete level reference')
        return MappingProxyType(out)
    except (OSError,ValueError,TypeError,KeyError):return MappingProxyType({})

@lru_cache(maxsize=1)
def break_catalog():
    try:
        out={}
        for r in _rows('weapon_break_skill_reference.csv'):
            m,s,i=(int(r[k]) for k in ('model_id','static_stage','skill_id'))
            d=int(r['display_level']) if r['display_level'] else None
            if m not in limits() or not 0<=s<=limits()[m][1] or (m,s) in out or i<0 or (s>0 and (i==0 or d!=s)):
                raise ValueError('invalid break reference')
            out[m,s]=BreakReference(i,d)
        if not out or set(out)!={(m,s) for m,(_,cap) in limits().items() for s in range(cap+1)}:raise ValueError('incomplete break reference')
        return MappingProxyType(out)
    except (OSError,ValueError,TypeError,KeyError):return MappingProxyType({})

def base_properties(model_id):
    return base_catalog().get(model_id) if type(model_id) is int else None

def level_reference(level):
    return level_catalog().get(level) if type(level) is int else None

def break_reference(model_id,static_stage):
    """Static stage lookup; does not infer stage from an absent packet rank."""
    return break_catalog().get((model_id,static_stage)) if type(model_id) is int and type(static_stage) is int else None
