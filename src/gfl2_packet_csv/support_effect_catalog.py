"""Static, ordered support skill effect candidates; never an evaluated combat formula."""
from dataclasses import dataclass
from .reference_data import lru_cache
from types import MappingProxyType
from .support_attribute_totals import _rows
from .support_capacity import special_stage_skills

@dataclass(frozen=True)
class Modifier:
    position: int
    property_id: int
    property_name: str
    arg3_raw: int | None
    arg4_raw: int | None
    arg5_raw: int
    condition_field18_static: str

@dataclass(frozen=True)
class BuffLink:
    position: int
    buff_id: int
    effect_buff_id: int | None
    link_arg4_raw: str
    trigger_field23_static: str
    status: str
    modifiers: tuple[Modifier, ...]

@dataclass(frozen=True)
class StageEffects:
    skill_group_id: int
    skill_id: int
    links: tuple[BuffLink, ...]

    @property
    def complete_reference_chain(self):
        return all(link.status == 'catalog_linked' for link in self.links)


def _ordered(rows):
    if [r.position for r in rows] != list(range(1,len(rows)+1)):
        raise ValueError('non-contiguous support effect positions')
    return tuple(rows)

@lru_cache(maxsize=1)
def effect_catalog():
    """Return immutable static references. Invalid catalogs raise; absent stage is not level 1."""
    effects={}; names={}; ids={}
    for r in _rows('support_skill_effect_candidates.csv', ('effect_buff_id','position','property_id','property_name','arg3_raw','arg4_raw','arg5_raw','condition_field18_static')):
        child,pos,pid=map(int,(r['effect_buff_id'],r['position'],r['property_id']))
        name=r['property_name']; a=int(r['arg3_raw']) if r['arg3_raw'] else None; b=int(r['arg4_raw']) if r['arg4_raw'] else None; mode=int(r['arg5_raw'])
        if child<=0 or pid<=0 or not name or (a is None)==(b is None) or mode not in (0,1) or names.get(name,pid)!=pid or ids.get(pid,name)!=name:
            raise ValueError('invalid support modifier')
        names[name]=pid;ids[pid]=name
        effects.setdefault(child,[]).append(Modifier(pos,pid,name,a,b,mode,r['condition_field18_static']))
    effects={k:_ordered(v) for k,v in effects.items()}
    skills={}; used=set(); known={s for _,s in special_stage_skills().values()}
    for r in _rows('support_skill_buff_links.csv', ('skill_id','position','buff_id','effect_buff_id','link_arg4_raw','trigger_field23_static','status')):
        skill,pos,buff=map(int,(r['skill_id'],r['position'],r['buff_id'])); child=int(r['effect_buff_id']) if r['effect_buff_id'] else None
        status=r['status']; arg=r['link_arg4_raw']; trigger=r['trigger_field23_static']
        if skill not in known or buff<=0: raise ValueError('unknown support skill or buff')
        if status=='catalog_linked':
            if child not in effects or not arg or not trigger: raise ValueError('invalid support buff reference')
            used.add(child); mods=effects[child]
        elif status=='missing_buff':
            if child is not None or arg or trigger: raise ValueError('invalid missing support buff')
            mods=()
        else: raise ValueError('invalid support buff status')
        skills.setdefault(skill,[]).append(BuffLink(pos,buff,child,arg,trigger,status,mods))
    if not known or set(skills)!=known or used!=set(effects): raise ValueError('incomplete support effect catalog')
    return MappingProxyType({k:_ordered(v) for k,v in skills.items()})


def stage_effect_candidates(model_id, level):
    """Explicit stage lookup only. Values are static arguments, not percentages or totals."""
    stage=special_stage_skills().get((model_id,level))
    if stage is None: return None
    group,skill=stage
    return StageEffects(group,skill,effect_catalog()[skill])
