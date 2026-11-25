"""Static common-key base properties and copy costs (탁본).

The packed option list can include innate properties. A matching property code
is not proof that the entire value is innate or that it cannot be reset.
The 0.5 percentage-point step is a candidate supported by the observed
stage-10 UI endpoint, not a verified progression for all stages.
"""
from dataclasses import dataclass
from .reference_data import lru_cache
from types import MappingProxyType
from .attachment_affixes import _rows
from .key_catalog import catalog as key_catalog
from .key_evolution import projection

_STAT_LABELS = MappingProxyType({9:'치명타', 10:'치명타 피해', 29:'공격 보너스'})
_ATTACHMENT_LABELS = MappingProxyType({1:'공격', 9:'치명타', 29:'공격 보너스'})

@dataclass(frozen=True)
class BaseProperty:
    property_id: int
    field_code: int
    value_units: int

@dataclass(frozen=True)
class CopyReference:
    limit: int
    item_id: int
    costs: tuple[int, ...]

@dataclass(frozen=True)
class EnhancementOption:
    code: int
    stage: int
    label_ko_candidate: str | None
    percentage_points_milli_candidate: int
    base_property_units: int | None
    base_relation: str

@lru_cache(maxsize=1)
def base_catalog():
    try:
        keys = key_catalog()
        result = {}
        for r in _rows('common_key_base_property_reference.csv'):
            k,p,f,v = (int(r[n]) for n in ('key_id','property_id','property_field_code','value_units'))
            if keys.get(k,(None,))[0]!=2 or min(p,f,v)<=0 or f in {x.field_code for x in result.get(k,())}:
                raise ValueError('invalid base property')
            if result.get(k) and result[k][0].property_id!=p:
                raise ValueError('conflicting property id')
            result.setdefault(k,[]).append(BaseProperty(p,f,v))
        if set(result)!={k for k,v in keys.items() if v[0]==2}:
            raise ValueError('missing base property reference')
        return MappingProxyType({k:tuple(v) for k,v in result.items()})
    except (OSError,ValueError,KeyError,TypeError):
        return MappingProxyType({})

@lru_cache(maxsize=1)
def copy_catalog():
    try:
        keys=key_catalog(); rows={}
        for r in _rows('common_key_copy_cost_reference.csv'):
            k,lim,item,num,cost=(int(r[n]) for n in ('key_id','copy_limit','cost_item_id','copy_number','cost_quantity'))
            if keys.get(k,(None,))[0]!=2 or min(lim,item,num,cost)<=0 or num>lim or lim>100:
                raise ValueError('invalid copy row')
            rows.setdefault(k,[]).append((num,lim,item,cost))
        out={}
        for k, entries in rows.items():
            entries.sort(); lim,item=entries[0][1:3]
            if len(entries)!=lim or any((num,l,i)!=(pos,lim,item) for pos,(num,l,i,_) in enumerate(entries,1)):
                raise ValueError('inconsistent copy cost rows')
            out[k]=CopyReference(lim,item,tuple(r[3] for r in entries))
        return MappingProxyType(out)
    except (OSError,ValueError,KeyError,TypeError):
        return MappingProxyType({})

def base_properties(key_id):
    return base_catalog().get(key_id) if type(key_id) is int else None

def copy_reference(key_id):
    """Static eligibility/cost reference, not the player's remaining copy count."""
    return copy_catalog().get(key_id) if type(key_id) is int else None

def attachment_stat_label(code):
    """Only screenshot-matched labels; other stat codes are not guessed."""
    return _ATTACHMENT_LABELS.get(code) if type(code) is int else None

def enhancement_options(key_id, packed):
    """Return (wire status, options). Preserve absent and unsupported states."""
    if type(key_id) is not int or (packed is not None and type(packed) is not int):
        return 'withheld_unrecognized', ()
    status,count,*values=projection(key_id,packed)
    if status!='structure_candidate':
        return status, ()
    refs=base_properties(key_id)
    base={r.field_code:r.value_units for r in refs or ()}
    options=[]
    for i in range(count):
        code,stage=values[2*i:2*i+2]; units=base.get(code)
        relation=('base_reference_unavailable' if refs is None else
                  'not_in_static_base' if units is None else
                  'base_code_and_candidate_value_match' if units==stage*5 else
                  'base_code_present_candidate_value_differs')
        options.append(EnhancementOption(code,stage,_STAT_LABELS.get(code),stage*500,units,relation))
    return status,tuple(options)
