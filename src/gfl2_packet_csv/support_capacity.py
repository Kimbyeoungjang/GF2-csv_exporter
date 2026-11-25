"""Static slot costs and explicit-stage skill references. Controlled removal confirmed observed used_capacity is occupied standard-slot load, not the level limit."""
from .reference_data import lru_cache
from .support_attribute_totals import _rows, level_totals
from .support_catalog import frame_slots, frame_slot_id, special_break_records, catalogs

@lru_cache(maxsize=1)
def slot_costs():
    try:
        result = {}
        for r in _rows('support_slot_capacity.csv', ('static_slot_id','size_code','capacity_candidate')):
            sid,size,cost=map(int,(r['static_slot_id'],r['size_code'],r['capacity_candidate']))
            if sid in result or size not in (1,2,3) or cost != 6*2**(size-1): return {}
            result[sid]=(size,cost)
        return result if set(result)==set(frame_slots().values()) else {}
    except (OSError,ValueError,KeyError,TypeError): return {}

@lru_cache(maxsize=1)
def chip_sizes():
    try:
        result={}
        for r in _rows('support_chip_size.csv', ('model_id','size_code')):
            model,size=int(r['model_id']),int(r['size_code'])
            if model in result or size not in (1,2,3): return {}
            result[model]=size
        return result if set(result)==catalogs().get('normal') else {}
    except (OSError,ValueError,KeyError,TypeError): return {}

def capacity_projection(level, formation, chips):
    maximum=level_totals().get(level,(None,)*7)[6]
    observed=formation['used_capacity']
    costs=slot_costs(); sizes=chip_sizes(); seen=set(); total=0
    if not costs or not sizes: return maximum,None,'catalog_unavailable'
    for frame in formation['frames'].values():
        for i,uid in enumerate(frame['slots'],1):
            if uid is None: continue
            slot=costs.get(frame_slot_id(frame['frame_id'],i))
            chip=chips['normal'].get(uid) if chips is not None else None
            if uid in seen or slot is None or chip is None or sizes.get(chip['model_id'])!=slot[0]:
                return maximum,None,'slot_reference_unavailable'
            seen.add(uid);total+=slot[1]
    if observed is None: status='field_absent'
    elif maximum is None: status='level_unavailable'
    elif observed==maximum==total: status='matches_both'
    elif observed==maximum: status='matches_level_limit'
    elif observed==total: status='matches_occupied_slots'
    else: status='value_mismatch'
    return maximum,total,status

@lru_cache(maxsize=1)
def special_stage_skills():
    try:
        result={}; breaks=special_break_records()
        for r in _rows('support_special_stage_skills.csv', ('model_id','level','break_record_id','skill_group_id','skill_id')):
            model,level,rid,group,skill=map(int,(r['model_id'],r['level'],r['break_record_id'],r['skill_group_id'],r['skill_id']))
            if (model,level) in result or breaks.get((model,level))!=rid or group<=0 or skill<=0: return {}
            result[model,level]=(group,skill)
        return result if set(result)==set(breaks) else {}
    except (OSError,ValueError,KeyError,TypeError): return {}

def stage_skill_projection(kind,model,level):
    if kind!='special' or level is None: return (None,None)
    return special_stage_skills().get((model,level),(None,None))
