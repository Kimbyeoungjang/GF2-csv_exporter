"""Separate doll key slots and strategy; never imply ownership or current equipment."""
from .account_extension import parse,msg,nums
from .formation_slots import _strategy,_catalogs
from .key_catalog import catalog
from .skill_parameters import COLUMNS as PARAM_COLUMNS, projection as param_projection

KEY_FILE='additional_doll_key_slots.csv'
VARIANT_FILE='additional_doll_skill_variants.csv'
SKILL_FILE='additional_doll_skills.csv'
HEADERS={KEY_FILE:'doll_id kind position key_id key_uid'.split(),
         VARIANT_FILE:'doll_id variant_position submode_code skill_count'.split(),
         SKILL_FILE:'doll_id variant_position position skill_id parameter_code'.split()+list(PARAM_COLUMNS)}
KEYS={KEY_FILE:'doll_id kind position'.split(),VARIANT_FILE:'doll_id variant_position'.split(),SKILL_FILE:'doll_id variant_position position'.split()}
KINDS={'fixed':(42,None,1),'common':(43,45,2),'expansion':(52,None,3),'bond':(59,60,4)}

def decode(data):
    r=msg(msg(parse(data),3),2)
    vectors={}
    for kind,(field,uid_field,_) in KINDS.items():
        if uid_field and ((field in r)!=(uid_field in r)):
            raise ValueError('unpaired additional key arrays')
        if field in r:
            ids=nums(r,field); uids=nums(r,uid_field) if uid_field else [None]*len(ids)
            if len(ids)!=len(uids) or len(ids)>64: raise ValueError('invalid additional key array size')
            vectors[kind]=list(zip(ids,uids))
    strategy=_strategy({8:r[57]} if 57 in r else {})
    return (vectors,strategy) if vectors or strategy is not None else None

def project(did, details, common, available):
    vectors,strategy=details
    out={name:[] for name in HEADERS}; counts={}
    keys=catalog(); _,skills,_=_catalogs()
    for kind,(_,uid_field,typ) in KINDS.items():
        values=vectors.get(kind);counts[kind]=len(values) if values is not None else None
        for pos,(kid,uid) in enumerate(values or [],1):
            if kid:
                entry=keys.get(kid)
                if entry is None or entry[0]!=typ or (not uid_field and entry[1]!=did): raise ValueError('additional key model mismatch')
            if uid_field:
                if uid is None or bool(kid)!=bool(uid): raise ValueError('additional key empty slot mismatch')
                if uid and (not available or common.get(uid,{}).get('key_id')!=kid): raise ValueError('additional key inventory reference not verified')
            elif uid is not None: raise ValueError('unexpected additional key uid')
            out[KEY_FILE].append(dict(zip(HEADERS[KEY_FILE],(did,kind,pos,kid,uid))))
    mode=strategy['mode'] if strategy is not None else None
    variants=strategy['variants'] if strategy is not None else []
    if mode is not None and (type(mode) is not int or not 0<=mode<2**32): raise ValueError('invalid additional skill mode')
    if len(variants)>16 or len({v[0] for v in variants})!=len(variants): raise ValueError('invalid additional variants')
    for i,(sub,entries) in enumerate(variants,1):
        if not 1<=sub<=255 or len(entries)>1024: raise ValueError('invalid additional skill variant')
        out[VARIANT_FILE].append(dict(zip(HEADERS[VARIANT_FILE],(did,i,sub,len(entries)))))
        for pos,(skill,param) in enumerate(entries,1):
            if skill not in skills or (param is not None and not 0<=param<2**32): raise ValueError('additional skill reference mismatch')
            out[SKILL_FILE].append(dict(zip(HEADERS[SKILL_FILE],(did,i,pos,skill,param,*param_projection(param)))))
    return out,{'key_counts':counts,'skill_setting_present':strategy is not None,'skill_mode_code':mode,'variant_count':len(variants)}

def validate(did,rows,meta,common,available,uint):
    """Rebuild typed sequences, then compare to a fresh projection."""
    vectors={}
    counts=meta['key_counts']
    if set(counts)!=set(KINDS): raise ValueError('invalid additional key count inventory')
    for kind,count in counts.items():
        if count is not None:
            if type(count) is not int or not 0<=count<=64: raise ValueError('invalid additional key count')
            vectors[kind]=[]
    for r in rows[KEY_FILE]:
        kind=r['kind']
        if kind not in vectors or uint(r['doll_id'],positive=True)!=did or uint(r['position'],positive=True)!=len(vectors[kind])+1: raise ValueError('invalid additional key sequence')
        vectors[kind].append((uint(r['key_id']),uint(r['key_uid'],optional=True)))
    if any(len(vectors[k])!=counts[k] for k in vectors): raise ValueError('additional key count mismatch')
    variants=[]; declared=[]
    for r in rows[VARIANT_FILE]:
        if uint(r['doll_id'],positive=True)!=did or uint(r['variant_position'],positive=True)!=len(variants)+1: raise ValueError('invalid additional variant sequence')
        variants.append((uint(r['submode_code'],positive=True),[]));declared.append(uint(r['skill_count']))
    for r in rows[SKILL_FILE]:
        i=uint(r['variant_position'],positive=True)-1
        if uint(r['doll_id'],positive=True)!=did or i>=len(variants) or uint(r['position'],positive=True)!=len(variants[i][1])+1: raise ValueError('invalid additional skill sequence')
        variants[i][1].append((uint(r['skill_id'],positive=True),uint(r['parameter_code'],optional=True)))
    if declared!=[len(v[1]) for v in variants]: raise ValueError('additional skill count mismatch')
    if type(meta['variant_count']) is not int or type(meta['skill_setting_present']) is not bool: raise ValueError('invalid additional skill presence')
    strategy={'mode':meta['skill_mode_code'],'variants':variants} if meta['skill_setting_present'] else None
    if strategy is None and (variants or meta['skill_mode_code'] is not None): raise ValueError('unexpected additional skill data')
    expected,expected_meta=project(did,(vectors,strategy),common,available)
    if meta!=expected_meta: raise ValueError('additional details metadata mismatch')
    for name,rs in expected.items():
        if rows[name]!=[{k:'' if v is None else str(v) for k,v in r.items()} for r in rs]: raise ValueError('additional details projection mismatch')
