"""Read-only 10833 task references. Membership is not completion semantics.

Does not change the V35 wire projection, infer missing records, or expose
withheld values. Group-specific mappings are static/observational candidates.
"""
from collections import Counter, defaultdict
from .reference_data import lru_cache
from types import MappingProxyType
from .doll_growth_catalog import _rows
from .task_reference_catalog import task_references, task_types
from .bundle_import import read_bundle

BP_GROUPS = MappingProxyType({3:(1,30),6:(2,30),45:(5,30),62:(2,93),63:(5,93)})
EXTRA_GROUPS = MappingProxyType({(11,4):('GuideQuestData',),(11,5):('GuideQuestPhaseData',),
    (41,4):('EventFreeSkinTasklistData',),(46,4):('KittyCafeDialogueData',),
    (70,4):('EventEndlessTowerTaskData','EventEndlessTowerRankData')})
COUNTS = {'GuideQuestData':60,'GuideQuestPhaseData':6,'EventFreeSkinTasklistData':80,
          'KittyCafeDialogueData':13,'EventEndlessTowerTaskData':25,'EventEndlessTowerRankData':25}

@lru_cache(maxsize=1)
def extra_catalog():
    data=defaultdict(dict)
    for r in _rows('progress_group_extra_reference.csv'):
        n=r['catalog']; i=int(r['reference_id']); p=int(r['parent_raw']) if r['parent_raw'] else None
        if n not in COUNTS or i<=0 or i in data[n] or (p is not None and p<=0):raise ValueError('invalid progress extra reference')
        data[n][i]=p
    if {k:len(v) for k,v in data.items()}!=COUNTS:raise ValueError('incomplete progress extra reference')
    phases=data['GuideQuestPhaseData']; guides=data['GuideQuestData']
    if any(v is not None for v in phases.values()) or Counter(guides.values())!={p:10 for p in phases}:raise ValueError('invalid guide phase reference')
    return MappingProxyType({k:MappingProxyType(v) for k,v in data.items()})

@lru_cache(maxsize=1)
def season_catalog():
    result={}
    for r in _rows('bp_season_reference.csv'):
        i=int(r['season_id']); lang=int(r['name_lang_id']); seq=int(r['field11_raw']); name=r['name_ko']
        if i in result or lang<=0 or seq<=0 or not name:raise ValueError('invalid season reference')
        result[i]=(name,lang,seq)
    if set(result)!=(set(range(30001,30041))|set(range(93001,93005))):raise ValueError('incomplete season reference')
    tasks=task_references()
    if not tasks or any(t.season_id not in result for t in tasks.values()):raise ValueError('missing task season reference')
    return MappingProxyType(result)

def progress_group_references(bundle):
    if 'account_progress_entries.csv' not in bundle.rows:
        return {'schema':'gf2-progress-group-references-1','status':'snapshot_unavailable','groups':{},'records':[]}
    extra=extra_catalog(); seasons=season_catalog(); tasks=task_references(); types=task_types()
    grouped=defaultdict(list)
    for r in bundle.rows['account_progress_entries.csv']:
        if not r['group_id_raw']:continue
        g=int(r['group_id_raw']); f=int(r['source_field']); k=(g,f)
        if k in EXTRA_GROUPS or (f==4 and g in BP_GROUPS):grouped[k].append(r)
    summaries={}; out=[]
    for (g,f), rows in sorted(grouped.items()):
        ids=Counter(r['value1_raw'] for r in rows if r['value1_raw']); statuses=Counter(); codes=Counter(); season_counts=Counter()
        for r in rows:
            ident=int(r['value1_raw']) if r['value1_raw'] else None
            refs=[]; t=tasks.get(ident)
            if f==4 and g in BP_GROUPS and t:
                category,family=BP_GROUPS[g]
                if t.category==category and t.season_id//1000==family:
                    refs.append({'catalog':'BpTaskData','category':category,'category_name_ko':types[category].name_ko,
                                 'season_id':t.season_id,'season_name_ko':seasons[t.season_id][0],
                                 'name_ko':t.name_ko,'description_ko':t.description_ko})
            for n in EXTRA_GROUPS.get((g,f),()):
                if ident in extra[n]:refs.append({'catalog':n,'parent_raw':extra[n][ident]})
            # The free-skin task IDs also overlap BpTaskData; keep both candidates.
            if (g,f)==(41,4) and t:
                refs.append({'catalog':'BpTaskData','category':t.category,'category_name_ko':types[t.category].name_ko,
                             'season_id':t.season_id,'season_name_ko':seasons[t.season_id][0],
                             'name_ko':t.name_ko,'description_ko':t.description_ko})
            status=('id_absent' if ident is None else 'duplicate_id' if ids[r['value1_raw']]>1 else
                    'multiple_reference_candidates' if len(refs)>1 else 'reference_candidate' if refs else 'reference_mismatch')
            observed=(int(r['value2_raw']) if r['decoded_pair']=='1' and r['value2_observation_status']=='binary_code_observed'
                      and r['value2_raw'] in ('0','1') and status!='duplicate_id' else None)
            statuses[status]+=1; codes[str(observed) if observed is not None else 'unresolved']+=1
            if status=='reference_candidate' and refs[0]['catalog']=='BpTaskData':season_counts[refs[0]['season_id']]+=1
            out.append({'group_id':g,'source_field':f,'reference_id':ident,'reference_status':status,
                        'references':refs,'observed_state_code':observed,'completion_status':'unconfirmed'})
        summaries[f'{g}.{f}']={'rows':len(rows),'reference_status_counts':dict(statuses),'state_code_counts':dict(codes),'season_counts':dict(sorted(season_counts.items()))}
    return {'schema':'gf2-progress-group-references-1','status':'static_candidates_not_completion_decoder','groups':summaries,'records':out}

def read_progress_group_references(path):
    return progress_group_references(read_bundle(path))
