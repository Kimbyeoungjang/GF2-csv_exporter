"""Static support skill text and direct buff references, not a combat simulator.
Markup, placeholders and effect arguments are intentionally not evaluated.
"""
from dataclasses import dataclass
from .reference_data import lru_cache
from types import MappingProxyType
from .support_attribute_totals import _rows
from .progression_reference_catalog import support_progression_catalog, _pos, _insert, _ordered


@dataclass(frozen=True)
class RawArgument:
    field_code: int
    raw_text: str


@dataclass(frozen=True)
class BuffReference:
    buff_id: int
    arguments: tuple[RawArgument, ...]


@dataclass(frozen=True)
class CombatSkill:
    skill_id: int
    name_lang_id: int | None
    description_lang_id: int | None
    name_ko: str | None
    description_ko: str | None
    direct_buffs: tuple[BuffReference, ...]
    arguments: tuple[RawArgument, ...]


@lru_cache(maxsize=1)
def combat_catalog():
    """Return read-only (skills, buffs), or None for an inconsistent catalog."""
    try:
        p=support_progression_catalog()
        if p is None: raise ValueError('missing progression catalog')
        known={}
        for stage in p[0].values():
            for link in stage.links:
                if link.battle_skill_id in known and known[link.battle_skill_id]!=link.display_present:
                    raise ValueError('inconsistent display flag')
                known[link.battle_skill_id]=link.display_present
        meta={}
        for row in _rows('support_combat_skills.csv',('skill_id','name_lang_id','description_lang_id','name_ko','description_ko')):
            sid=_pos(row['skill_id']); vals=tuple(row[k] for k in ('name_lang_id','description_lang_id','name_ko','description_ko'))
            if sid not in known or (known[sid] and not all(vals)) or (not known[sid] and any(vals)):
                raise ValueError('invalid display metadata')
            _insert(meta,sid,(_pos(vals[0]),_pos(vals[1]),vals[2],vals[3]) if known[sid] else (None,)*4)
        if set(meta)!=set(known): raise ValueError('incomplete skill metadata')
        buffs={}
        for row in _rows('support_combat_buffs.csv',('buff_id',)):
            _insert(buffs,_pos(row['buff_id']),None)
        args={}; counts={}
        allowed={'skill':(meta,{17,25,26,27,44}),'buff':(buffs,{17,18,21,22,23,30,33,58,63})}
        for row in _rows('support_combat_arguments.csv',('owner_kind','owner_id','field_code','raw_text')):
            kind=row['owner_kind']; ident=_pos(row['owner_id']); f=_pos(row['field_code']); text=row['raw_text']
            owners,fields=allowed[kind]
            if ident not in owners or f not in fields or text is None: raise ValueError('invalid raw argument')
            _insert(args.setdefault((kind,ident),{}),f,RawArgument(f,text))
            counts[kind,f]=counts.get((kind,f),0)+1
        # Fields 23 and 33 were required for every supported buff in V35.
        if not meta or not buffs or any(not {23,33} <= set(args.get(('buff', b), {})) for b in buffs):
            raise ValueError('incomplete arguments')
        def get_args(kind,ident):
            values=args.get((kind,ident),{});return tuple(values[f] for f in sorted(values))
        buffs={b:BuffReference(b,get_args('buff',b)) for b in buffs}
        links={}; used=set()
        for row in _rows('support_combat_buff_links.csv',('skill_id','position','buff_id')):
            sid,pos,bid=(_pos(row[k]) for k in ('skill_id','position','buff_id'))
            if sid not in meta or bid not in buffs: raise ValueError('invalid direct link')
            _insert(links.setdefault(sid,{}),pos,buffs[bid]);used.add(bid)
        if used!=set(buffs):
            raise ValueError('incomplete direct links')
        skills={s:CombatSkill(s,*m,_ordered(links.get(s,{})),get_args('skill',s)) for s,m in meta.items()}
        return MappingProxyType(skills),MappingProxyType(buffs)
    except (OSError,ValueError,TypeError,KeyError):
        return None


def combat_skill(skill_id):
    c=combat_catalog();return c[0].get(skill_id) if c is not None and type(skill_id) is int else None


def combat_buff(buff_id):
    c=combat_catalog();return c[1].get(buff_id) if c is not None and type(buff_id) is int else None


def stage_combat_references(record_id):
    """Retain RC100 field code/position and repeated references at each stage."""
    p=support_progression_catalog();c=combat_catalog()
    if p is None or c is None or type(record_id) is not int:return None
    stage=p[0].get(record_id)
    return tuple((link,c[0][link.battle_skill_id]) for link in stage.links) if stage is not None else None
