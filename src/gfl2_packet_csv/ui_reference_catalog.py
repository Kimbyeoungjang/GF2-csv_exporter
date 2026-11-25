"""Static Korean UI references; never override controlled account observations."""
from collections import defaultdict
from dataclasses import dataclass
from .reference_data import lru_cache
from .reference_data import files
from types import MappingProxyType
import csv
from .skill_parameters import PRIORITIES

@dataclass(frozen=True)
class PartSlot:
    position: int
    group_id: int
    translation_id: int
    label_ko: str
    options: tuple[tuple[int, int], ...]

@dataclass(frozen=True)
class PartSelection:
    slot: PartSlot
    index: int
    option_id: int | None
    status: str

@dataclass(frozen=True)
class UILabel:
    text: str
    translation_id: int
    status: str

# A direct experiment and static localization disagree for Sakura's slot 3.
# Do not resolve this by silently replacing either source.
LABEL_CONFLICTS = MappingProxyType({(1106801, 3): '하의'})

def _rows(name):
    with files('gfl2_packet_csv.static_tables').joinpath(name).open('r', encoding='utf-8-sig', newline='') as f:
        yield from csv.DictReader(f)

@lru_cache(maxsize=1)
def costume_catalog():
    groups = defaultdict(dict)
    owners, option_ids = {}, set()
    for r in _rows('costume_part_options_ko.csv'):
        cid,pos,g,t,opt,oid = (int(r[k]) for k in ('costume_id','slot_position','group_id','translation_id','option_index','option_id'))
        label = r['label_ko']
        if min(cid,pos,g,t,opt,oid)<=0 or not label or pos>4 or opt>15 or oid in option_ids:
            raise ValueError('invalid costume part catalog record')
        if g in owners and owners[g] != (cid,pos): raise ValueError('duplicate costume group')
        owners[g]=(cid,pos); option_ids.add(oid)
        group=groups[cid].setdefault(pos,(g,t,label,{}))
        if group[:3] != (g,t,label) or opt in group[3]: raise ValueError('inconsistent costume group')
        group[3][opt]=oid
    result={}
    for cid,slots in groups.items():
        if sorted(slots)!=list(range(1,len(slots)+1)): raise ValueError('noncontiguous costume slots')
        result[cid]=tuple(PartSlot(p,g,t,label,tuple(sorted(opts.items()))) for p,(g,t,label,opts) in sorted(slots.items()))
        if any([i for i,_ in s.options]!=list(range(1,len(s.options)+1)) for s in result[cid]): raise ValueError('noncontiguous costume options')
    return MappingProxyType(result)

def costume_selection(costume_id, state):
    """Return static labels plus selections, or a separate unavailable status.

    The label conflict is reported per slot; invalid states are never repaired.
    A matching option does not prove UI semantics on every untested model.
    """
    slots=costume_catalog().get(costume_id)
    if slots is None: return 'unknown_costume', ()
    if state is None: return 'field_absent', ()
    if type(state) is not int or not 0<=state<1<<(4*len(slots)):
        return 'out_of_slot_range', ()
    selections=[]
    for slot in slots:
        idx=(state>>(4*(slot.position-1)))&15
        oid=dict(slot.options).get(idx)
        status='unknown_option' if oid is None else 'static_label_conflict' if (costume_id,slot.position) in LABEL_CONFLICTS else 'static_reference_candidate'
        selections.append(PartSelection(slot,idx,oid,status))
    return ('option_out_of_range' if any(s.option_id is None for s in selections) else 'static_label_conflict' if any(s.status=='static_label_conflict' for s in selections) else 'static_reference_candidate'),tuple(selections)

@lru_cache(maxsize=1)
def _directives():
    result={}
    for r in _rows('ai_directive_labels_ko.csv'):
        key=(r['source_table'],int(r['static_code']))
        if key in result: raise ValueError('duplicate directive label')
        result[key]=UILabel(r['label_ko'],int(r['translation_id']),'static_reference')
    return MappingProxyType(result)

def conditional_label(code):
    return _directives().get(('AiStrategyData',code))

def movement_label(code):
    return _directives().get(('AiMoveData',code))

def condition_type_label(packet_code):
    if type(packet_code) is not int or packet_code%2: return None
    return _directives().get(('AiConditionData',packet_code//2))

@lru_cache(maxsize=1)
def _priority_labels():
    result={}
    for r in _rows('ai_priority_label_order_candidates_ko.csv'):
        kind,code=int(r['condition_type_code']),int(r['priority_code_candidate'])
        key=(kind,code)
        if key in result: raise ValueError('duplicate priority label')
        result[key]=UILabel(r['label_ko'],int(r['translation_id']),'controlled_mapping' if code in PRIORITIES.get(kind,{}) else 'localization_order_candidate')
    return MappingProxyType(result)

def priority_label(kind, code):
    """Untested numeric mappings remain candidates, not verified game codes."""
    return _priority_labels().get((kind,code))
