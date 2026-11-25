"""Static skill target classification candidates; do not infer target semantics.

BattleSkillData field 15 is a bitmask. AiChooseTypeData field 2 lists
mask members. The observed parameter prefix is twice the matching group ID.
Multiple matching groups are deliberately not resolved by priority.
"""
from dataclasses import dataclass
from .reference_data import lru_cache
from types import MappingProxyType
from .ui_reference_catalog import _rows
from .skill_parameters import projection

@dataclass(frozen=True)
class TargetReference:
    mask: int | None
    group_candidates: tuple[int, ...]
    prefix_candidates: tuple[int, ...]
    status: str

@lru_cache(maxsize=1)
def choose_masks():
    groups={}
    seen=set()
    for row in _rows('ai_choose_type_masks.csv'):
        code, member=int(row['choose_type_code']),int(row['mask_member'])
        if code not in (1,2,3) or member<=0 or member&(member-1) or member in seen:
            raise ValueError('invalid choose type mask member')
        seen.add(member)
        groups[code]=groups.get(code,0)|member
    if set(groups)!={1,2,3}: raise ValueError('incomplete choose type catalog')
    return MappingProxyType(groups)

@lru_cache(maxsize=1)
def skill_masks():
    result={}
    for row in _rows('ai_skill_target_reference.csv'):
        skill,mask=int(row['skill_id']),int(row['static_field15_mask'])
        if skill<=0 or mask<=0 or skill in result: raise ValueError('invalid skill target catalog')
        result[skill]=mask
    return MappingProxyType(result)

def target_reference(skill_id):
    mask=skill_masks().get(skill_id)
    if mask is None: return TargetReference(None,(),(),'static_mask_unavailable')
    groups=tuple(k for k,v in sorted(choose_masks().items()) if mask&v)
    status={0:'no_matching_group',1:'single_group_candidate'}.get(len(groups),'multiple_group_candidates')
    return TargetReference(mask,groups,tuple(k*2 for k in groups),status)

def prefix_reference_status(skill_id, raw):
    """Compare without discarding raw data or choosing an ambiguous group."""
    prefix=projection(raw)[0]
    if prefix is None: return 'parameter_unavailable'
    ref=target_reference(skill_id)
    if not ref.prefix_candidates: return ref.status
    if prefix not in ref.prefix_candidates: return 'prefix_reference_mismatch'
    return 'single_group_match' if len(ref.prefix_candidates)==1 else 'multiple_group_match'
