"""Offline static joins and optional bundle-bound private state CSV. No packet parsing."""
from collections import Counter
from fractions import Fraction as F
from pathlib import Path
from .reference_data import files
import csv, json, hashlib, io
from .panel_ledger import index
from .bundle_import import bound_archive_digest
FIELDS=('schema','bundle_sha256','doll_id','management_stage_candidate','oath_flag_raw','oath_paired_field_present','food_field61_raw')
SCHEMA='rc129-panel-candidates-1'
EXPECTED_FIELDS={(1,0):{2},(1,1):{5},(1,2):{3},(2,0):{39},(2,1):{35},(2,2):{36},(2,3):{37},(2,4):{38},(2,5):{42},(2,31):{35,36,37,38,42}}

def digest(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()


def csv_snapshot(path, limit):
    """Bound the bytes actually consumed; avoid stat/open replacement races."""
    if isinstance(path, bytes):
        data = path
    else:
        with Path(path).open("rb") as f:
            data = f.read(limit + 1)
    if len(data) > limit:
        raise ValueError("state CSV too large")
    return io.StringIO(data.decode("utf-8-sig"), newline="")


def uint(value, optional=False):
    if optional and value=='':return None
    if not isinstance(value,str) or not value.isascii() or not value.isdecimal() or len(value)>20:
        raise ValueError('invalid candidate integer')
    n=int(value)
    if str(n)!=value or n>=2**64:raise ValueError('noncanonical candidate integer')
    return n


def read_candidates(path,bundle_path,bundle,control):
    sha=bound_archive_digest(bundle_path,bundle); result={}; levels=set()
    ids=set(index(bundle.rows['dolls.csv'],'doll_id'))
    if not bundle.complete_capture:raise ValueError('incomplete bundle')
    with csv_snapshot(path, 1024*1024) as f:
        reader=csv.DictReader(f)
        if tuple(reader.fieldnames or ())!=FIELDS:raise ValueError('candidate CSV schema mismatch')
        for r in reader:
            if None in r or any(v is None for v in r.values()) or r['schema']!=SCHEMA or r['bundle_sha256']!=sha:
                raise ValueError('candidate CSV/bundle mismatch')
            did=uint(r['doll_id']); mg=uint(r['management_stage_candidate']); flag=uint(r['oath_flag_raw'],True)
            paired=uint(r['oath_paired_field_present']); food=uint(r['food_field61_raw'],True)
            if did not in ids or did in result or mg not in control or paired not in (0,1):raise ValueError('invalid or duplicate candidate record')
            levels.add(mg)
            oath=True if flag==1 and paired else False if flag is None and not paired else None
            result[did]={'oath_candidate':oath,'food_field61_raw':food}
    if set(result)!=ids or len(levels)!=1:raise ValueError('candidate CSV coverage or stage mismatch')
    return levels.pop(),result


def read_panel_state(path,bundle_path,bundle,control,food):
    """Read normalized login observations, not packed wire scalars or timestamps."""
    from .panel_state import FIELDS as columns, SCHEMA as schema, LEGACY_FIELDS, LEGACY_SCHEMA, V2_FIELDS, V2_SCHEMA
    from .embedded_states import bound_state_digest
    sha=bound_state_digest(path,bundle_path,bundle,'panel_state'); ids=set(index(bundle.rows['dolls.csv'],'doll_id'))
    if not bundle.complete_capture:
        raise ValueError('invalid panel state input')
    out={}; levels=set()
    with csv_snapshot(path, 1024*1024) as f:
        reader=csv.DictReader(f)
        legacy=tuple(reader.fieldnames or ())==LEGACY_FIELDS
        v2=tuple(reader.fieldnames or ())==V2_FIELDS
        if not legacy and not v2 and tuple(reader.fieldnames or ())!=columns:raise ValueError('panel state schema mismatch')
        for r in reader:
            if None in r or any(v is None for v in r.values()) or r['schema']!=(LEGACY_SCHEMA if legacy else V2_SCHEMA if v2 else schema) or r['bundle_sha256']!=sha:
                raise ValueError('panel state/bundle mismatch')
            did=uint(r['doll_id']); mg=uint(r['management_stage_candidate'],True)
            if did not in ids or did in out or (mg is not None and mg not in control):raise ValueError('invalid panel state record')
            levels.add(mg)
            oath=r['oath_state_candidate']; observation=r['food_observation']
            if oath not in ('positive_flag','both_absent','unknown'):raise ValueError('invalid oath candidate')
            if legacy:
                a,e,u=(uint(r[k],True) for k in ('attack_xp_candidate','acid_xp_candidate','unmapped_food_nonzero'))
                if observation=='observed':
                    if a is None or e is None or a>255 or e>255 or u not in (0,1):raise ValueError('invalid food candidate')
                    # V1's acid label is obsolete: its numeric value is a high-byte observation, not acid-specific.
                    # If any middle byte was nonzero, defense and HP cannot be recovered.
                    packed=packed_food_slots(food,a,None if u else 0,None if u else 0,bool(e),bool(u),element_xp=e)
                elif observation in ('absent','unavailable') and a is None and e is None and u is None:
                    packed=packed_food_candidate(food,None)
                else:raise ValueError('invalid food observation')
            else:
                a,d,h,e,u=(uint(r[k],True) for k in ('attack_xp_candidate','defense_xp_candidate','hp_xp_candidate','element_food_nonzero' if v2 else 'element_xp_candidate','unmapped_food_nonzero'))
                if observation=='observed':
                    if any(x is None or x>255 for x in (a,d,h)) or e is None or e>(1 if v2 else 255) or u not in (0,1):raise ValueError('invalid food candidate')
                    packed=packed_food_slots(food,a,d,h,bool(e),bool(u),element_xp=(None if e else 0) if v2 else e)
                elif observation in ('absent','unavailable') and all(x is None for x in (a,d,h,e,u)):
                    packed=packed_food_candidate(food,None)
                else:raise ValueError('invalid food observation')
            if observation=='unavailable':packed['status']='unavailable'
            out[did]={'oath_candidate':{'positive_flag':True,'both_absent':False,'unknown':None}[oath], 'packed_food':packed}
    if set(out)!=ids or len(levels)!=1:raise ValueError('panel state coverage or stage mismatch')
    return levels.pop(),out


def food_catalog(food):
    """All primary/secondary groups; preserve the all-element group separately."""
    out={}
    for _,p,s,stage,threshold,_,_ in food:
        stages=out.setdefault((p,s),{})
        if stage in stages or threshold<=0:raise ValueError('invalid food catalog')
        stages[stage]=threshold
    if any(set(v)!={1,2,3} for v in out.values()) or not {(1,0),(1,1),(1,2)}<=set(out):raise ValueError('incomplete food catalog')
    return out


def read_static_csv(directory=None):
    """Use existing, version-matched research references without proprietary table archives.
    SHA manifest is checked at package extraction; these files are trusted static input,
    not proof of the underlying mechanics. Reject duplicate reference keys.
    """
    directory=Path(directory) if directory is not None else files("gfl2_packet_csv.static_tables")
    def rows(name):
        with (directory/('RC129_'+name+'_SAFE.csv')).open(encoding='utf-8-sig',newline='') as f:
            yield from csv.DictReader(f)
    def put(out,key,value):
        if key in out:raise ValueError('duplicate static CSV reference')
        out[key]=value
    def vals(r,columns):return tuple(int(r[c]) for c in columns)
    control={}; food=[]; food_ids=set()
    for r in rows('ACCOUNT_BONUS_STATIC'):
        if r['source']=='ControlRoomData':
            level,pid=vals(r,('record_id','property_id')); p,roles=control.setdefault(level,(pid,{}))
            if p!=pid:raise ValueError('management reference mismatch')
            put(roles,r['role_or_primary'],vals(r,('attack','defense','hp')))
        elif r['source']=='LobbyCookPropertyData':
            rid,primary,secondary,stage,threshold,pid=vals(r,('record_id','role_or_primary','secondary','stage','threshold_raw','property_id'))
            if rid in food_ids:raise ValueError('duplicate food id')
            food_ids.add(rid)
            food.append((rid,primary,secondary,stage,threshold,pid,{int(k):int(v) for k,v in json.loads(r['property_fields_or_pairs']).items()}))
    if any(set(v[1])!={'Guard','Scout','Assist','Suppress'} for v in control.values()) or not control or set(control)!=set(range(1,max(control)+1)) or len(food)!=30:raise ValueError('incomplete account references')
    roles={}; refs={}; links={}
    for r in rows('DOLL_ROLE_REFERENCE'):
        put(roles,int(r['doll_model_id']),(int(r['GunData_field13']),r['GunDutyData_field3']))
    for r in rows('CORE_CHIP_PROPERTY'):
        model,stage,rid,pid=vals(r,('model_id','stage','break_record_id','property_id'))
        put(refs,(model,stage),(rid,pid,vals(r,('attack_bonus_raw','defense_bonus_raw','hp_bonus_raw','crit_damage_raw')),tuple(tuple(x) for x in json.loads(r['property_new_pairs']))))
    for r in rows('EMBLEM_STAGE_PROPERTY_REFERENCE'):
        put(links,int(r['item_id']),(*vals(r,('stage_id','property_id')),vals(r,('attack','defense','hp'))))
    from .doll_growth_catalog import core_models
    if set(roles)!=set(core_models()) or not refs or not links:raise ValueError('incomplete role/chip/emblem reference')
    from .support_catalog import special_break_records
    breaks = special_break_records()
    if not breaks or set(refs)!=set(breaks) or any(refs[k][0]!=breaks[k] for k in refs):
        raise ValueError('incomplete chip stages')
    scales={}; love={}; cores={}; control_suppress={}; foods={}
    for r in rows('STAT_BREAKDOWN_STATIC'):
        rid=int(r['record_id']); source=r['source']
        if source=='LoveTalentKeyData':put(love,rid,int(r['other_raw']));continue
        v=vals(r,('attack_raw','defense_raw','hp_raw'))
        if source=='CoreData':put(cores,rid,v);continue
        target={'GunLevelExpData':scales,'ControlRoomData_suppress':control_suppress,'LobbyCookPropertyData':foods}[source]
        put(target,rid,(int(r['property_id']),v))
    if not scales or not love or not cores or set(control_suppress)!=set(control) or set(foods)!=food_ids:raise ValueError('incomplete growth reference')
    return control,food,refs,roles,links,(scales,love,cores,control_suppress,foods)


def base_auxiliary_panel(components):
    """Direct doll-base candidates, not scaled LevelUpData or final battle effects.
    PropertyData names verified against static DLL: 7 MaxAp, 8 MaxWillValue,
    68 SuppressResist. UI units corroborated by two doll observations.
    """
    bases=[dict(c.values) for c in components if c.source=='doll_base']
    if len(bases)!=1:return None
    b=bases[0]
    if not {7,8,68}<=set(b):return None
    return {'movement':str(F(b[7],100)),'stability':str(F(b[8])),
            'stability_defense_pp':str(F(b[68],10)), 'status':'doll_base_candidate_not_all_effects'}


def oath_from_affinity(level, flag_candidate, *, scenario=False):
    """User confirms progression beyond 5 requires a vow. Level 5 itself does not prove absence.
    Conflicting packet candidate is not silently overridden.
    """
    if level is not None and 6<=level<=9:
        return (True,'affinity_above_5_requires_oath') if flag_candidate is not False else (None,'affinity_flag_conflict')
    return (flag_candidate,'flag_presence_hypothesis') if scenario else (None,'unresolved')



def affinity_gate_projection(level, xp, reference_status, flag_candidate=None):
    """Current eligibility, not irreversible history. A negative vow candidate
    assumes that earned affinity is not reduced after a vow. The Lv.5 upper
    boundary is not classified as negative; a positive flag below the gate is
    a contradiction, not something to overwrite. Requires a consistent XP.
    """
    from .doll_growth_catalog import affinity_thresholds
    bounds=affinity_thresholds()
    gate=bounds.get(5,(None,None))[1]
    out={'status':'unresolved','gate_xp_candidate':gate,'eligible_candidate':None,
         'vowed_candidate':None,'basis':'affinity_state_unavailable'}
    if gate is None or level not in bounds:return out
    if level>5:
        if flag_candidate is False:
            out.update(status='conflict',basis='affinity_flag_conflict')
        else:out.update(status='candidate',eligible_candidate=True,vowed_candidate=True,basis='affinity_above_5_requires_oath')
        return out
    lo,hi=bounds[level]
    if reference_status!='threshold_consistent' or type(xp) is not int or xp<lo or (hi is not None and xp>hi):return out
    eligible=xp>=gate
    if not eligible and flag_candidate is True:
        out.update(status='conflict',eligible_candidate=False,basis='positive_flag_below_gate')
    elif not eligible:
        out.update(status='candidate',eligible_candidate=False,vowed_candidate=False,
                   basis='below_vow_gate_no_affinity_decrease_hypothesis')
    else:
        out.update(status='candidate',eligible_candidate=True,basis='gate_reached_vow_unknown')
    return out


def basic_food_metric_stage(state,name):
    """Do not bypass the unmapped-byte guard on the per-stat fallback path."""
    if state is None or state.get('status')!='partial_byte_layout_candidate' or state.get('unmapped_nonzero') is not False:
        return None
    return state.get(name+'_stage')

def emblem_total(rows, links, *, inventory_complete):
    """Only a complete inventory with quantities 0/1 is covered by evidence."""
    if not inventory_complete:
        raise ValueError('item snapshot not established')
    owned = index(rows, 'item_id')
    if any(r['item_category_code'] == '150' and i not in links for i, r in owned.items()):
        raise ValueError('unlinked attribute item')
    totals = [0]*3
    for i, (_, _, vals) in links.items():
        if i not in owned:
            continue  # caller must establish full item snapshot
        r = owned[i]
        if r['item_category_code'] != '150' or int(r['quantity']) not in (0, 1):
            raise ValueError('unsupported attribute item category or quantity')
        if int(r['quantity']) == 1:
            totals = [a+b for a,b in zip(totals,vals)]
    return tuple(totals)


def management_for_model(model,stage,roles,control):
    if model not in roles or stage not in control: raise ValueError('missing model or stage')
    return control[stage][1][roles[model][1]]


def cooperation(rows, refs):
    settings=rows['support_settings.csv']
    if len(settings)!=1 or not settings[0]['selected_position_raw']: raise ValueError('selected formation absent')
    selected=settings[0]['selected_position_raw']
    formations=[r for r in rows['support_formations.csv'] if r['position']==selected]
    if len(formations)!=1 or formations[0]['special_slots_present']!='1': raise ValueError('special slot state absent')
    special=[r for r in rows['support_chips.csv'] if r['kind']=='special']; chips={r['uid']:r for r in special}
    if len(chips)!=len(special): raise ValueError('duplicate chip UID')
    slots=[r for r in rows['support_special_slots.csv'] if r['formation_position']==selected]
    if not slots or sorted(int(r['slot_position']) for r in slots)!=list(range(1,len(slots)+1)): raise ValueError('missing or duplicate special slots')
    slots.sort(key=lambda r:int(r['slot_position']))
    totals=[0]*4; extra={}; details=[]; seen=set()
    for r in slots:
        uid=r['uid']
        if uid in ('','0'):continue
        if uid in seen: raise ValueError('duplicate equipped chip')
        seen.add(uid)
        c=chips.get(uid)
        if not c or c['kind']!='special': raise ValueError('unresolved special slot')
        raw=c['player_amplification_level']; stage=int(raw) if raw else 1
        model=int(c['model_id']); rid,pid,vals,pairs=refs[model,stage]
        if raw and c.get('amplification_break_record_id')!=str(rid): raise ValueError('break record mismatch')
        for i,v in enumerate(vals):totals[i]+=v
        for code,value in pairs:extra[code]=extra.get(code,0)+value
        details.append({'slot':int(r['slot_position']),'model':model,'stage':stage,'stage_status':'explicit' if raw else 'absent_first_stage_candidate','break_id':rid,'property_id':pid,'bonus_raw':vals,'extra_raw':dict(pairs)})
    return {'slots':details,'bonus_pp':[str(F(v,10)) for v in totals[:3]],'crit_damage_pp':str(F(totals[3],10)), 'property_new_raw':extra,'status':'candidate' if any(d['stage_status']!='explicit' for d in details) else 'explicit_stage_static_reference'}


def food_stage_values(food, stages):
    """Current-stage values only; requires explicit observed/decoded stages."""
    refs={(p,s,l):v for _,p,s,l,_,_,v in food}
    if len(refs)!=len(food):raise ValueError('duplicate food stage')
    if len(stages)!=3 or any(type(l) is not int or l not in range(4) for l in stages):raise ValueError('unknown food stage')
    return tuple(0 if l==0 else refs[1,s,l][c] for s,(c,l) in enumerate(zip((2,5,3),stages)))


def group_catalog(food):
    groups={}
    for rid,p,s,stage,threshold,pid,props in food:
        g=(p,s); tiers=groups.setdefault(g,{})
        if g not in EXPECTED_FIELDS or set(props)!=EXPECTED_FIELDS[g] or stage not in (1,2,3) or stage in tiers or threshold<=0:
            raise ValueError('invalid food group reference')
        tiers[stage]=(threshold,pid,dict(props),rid)
    if set(groups)!=set(EXPECTED_FIELDS) or any(set(t)!={1,2,3} for t in groups.values()):raise ValueError('incomplete food group catalog')
    return groups


def food_bytes(raw):
    if raw is None:
        return None
    if type(raw) is not int or not 0 <= raw < 2**64:
        raise ValueError("invalid food uint64")
    return tuple(raw.to_bytes(8, "little"))


def packed_food_candidate(food, raw):
    """Basic groups observed at bytes 0..2. Byte 7 is shared by all tested element foods."""
    slots=food_bytes(raw)
    if slots is None:
        return {"status":"field_absent", **{f"{name}_{part}":None for name in ("attack","defense","hp") for part in ("xp","stage")},
                "acid_xp":None,"acid_stage":None,"element_food_nonzero":None,"element_xp":None,"unmapped_nonzero":None}
    return packed_food_slots(food,*slots[:3],bool(slots[7]),any(slots[3:7]),element_xp=slots[7])


def packed_food_slots(food,attack,defense,hp,element_food_nonzero,unmapped_nonzero,*,element_xp=None):
    catalog=group_catalog(food)
    def stage(group,xp):
        thresholds=catalog[group]
        cap=sum(v[0] for v in thresholds.values())
        if xp is None or xp>cap:return None
        return sum(xp>=sum(thresholds[i][0] for i in range(1,k+1)) for k in range(1,4))
    return {"status":"partial_byte_layout_candidate",
            "attack_xp":attack,"defense_xp":defense,"hp_xp":hp,
            "attack_stage":stage((1,0),attack),"defense_stage":stage((1,1),defense),"hp_stage":stage((1,2),hp),
            # Preserve nullable deprecated API keys; never present byte 7 as acid.
            "acid_xp":None,"acid_stage":None,
            "element_food_nonzero":element_food_nonzero,"element_xp":element_xp,"unmapped_nonzero":unmapped_nonzero}


def basic_food_scenario(food,raw):
    """Explicit scenario only; absent whole field as zero remains a hypothesis."""
    return basic_food_state_scenario(packed_food_candidate(food,raw))


def basic_food_state_scenario(state):
    if state['status']=='field_absent':return (0,0,0)
    if state['status']!='partial_byte_layout_candidate' or state["unmapped_nonzero"]:return None
    stages=tuple(state[k+"_stage"] for k in ("attack","defense","hp"))
    return stages if all(x is not None for x in stages) else None


def read_doll_food_groups(directory=None):
    """GunData field22 -> food secondary group, corroborated by seven gift observations.
    Missing field22 is a zero/physical candidate, not proof of an explicit scalar.
    """
    directory=Path(directory) if directory is not None else files("gfl2_packet_csv.static_tables")
    result={}
    with (directory/'RC129_DOLL_FOOD_GROUP_SAFE.csv').open(encoding='utf-8-sig',newline='') as f:
        for row in csv.DictReader(f):
            model=uint(row['doll_model_id']); group=uint(row['food_secondary_group_candidate']); status=row['static_field_status']
            if model in result or (2,group) not in EXPECTED_FIELDS or status not in ('explicit','absent_zero_candidate') or (status=='absent_zero_candidate' and group!=0):
                raise ValueError('invalid doll food group reference')
            result[model]=(group,status)
    from .doll_growth_catalog import core_models
    if set(result)!=set(core_models()):raise ValueError('incomplete doll food groups')
    return result


def element_food_projection(food,state,reference,*,scenario=False):
    """Group-specific food candidate, not a verified final damage stat.

    All seven group assignments are observed; cumulative thresholds and units
    for element food are not yet verified at a completed stage. Preserve raw
    static property values; never split all-element food into independent XP.
    """
    out={'status':'unresolved','group_candidate':None,'group_basis':None,
         'xp_candidate':None,'stage_candidate':None,'property_raw_candidate':None}
    if reference is None:return out
    group,basis=reference
    out.update(group_candidate=group,group_basis=basis)
    if state is None or state['status']=='unavailable':return out
    xp=state.get('element_xp')
    if state['status']=='field_absent' and scenario:xp=0
    out['xp_candidate']=xp
    if xp is None or state.get('unmapped_nonzero'):return out
    tiers=group_catalog(food)[2,group]
    cap=sum(v[0] for v in tiers.values())
    if type(xp) is not int or not 0<=xp<=cap:return out
    stage=sum(xp>=sum(tiers[i][0] for i in range(1,k+1)) for k in range(1,4))
    out.update(status='candidate',stage_candidate=stage,property_raw_candidate={} if stage==0 else dict(tiers[stage][2]),
               stage_basis='absent_zero_hypothesis' if state['status']=='field_absent' else 'element_cumulative_threshold_hypothesis',
               unit_status='unverified')
    return out
