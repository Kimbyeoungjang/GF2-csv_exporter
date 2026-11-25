"""Cross-group reference evidence, not a progress or reward completion decoder.

Diagnostics operate on validated V35 CSV projections and return only counts.
Missing groups, malformed pairs, duplicates and unknown IDs never imply absence
of an account record or completion of a task.
"""
from collections import Counter, defaultdict
from .reference_data import lru_cache
from types import MappingProxyType
from .doll_growth_catalog import _rows
from .progress_catalog import catalogs


@lru_cache(maxsize=1)
def relation_catalog():
    """Return immutable complete static relations, or None on invalid data."""
    try:
        ids = catalogs()
        groups = defaultdict(list)
        task_ids = set()
        for r in _rows('control_room_task_links.csv'):
            g, pos, t, f2, f3, f9 = (int(r[k]) for k in ('group_id','position','task_id','task_field2','task_field3','task_field9'))
            if min(g,pos,t,f2,f3,f9) <= 0 or g != f2 or t in task_ids:
                raise ValueError('invalid control room reference')
            task_ids.add(t); groups[g].append((pos,t,f3,f9))
        if set(groups) != ids['ControlRoomTaskByGroupData'] or task_ids != ids['ControlRoomTaskData']:
            raise ValueError('incomplete control room references')
        for g, links in groups.items():
            links.sort()
            if [r[0] for r in links] != [1,2,3] or {r[2] for r in links} != {1,2,3}:
                raise ValueError('invalid control room task order or type')
        sub = {}
        for r in _rows('amo_activity_sub_links.csv'):
            i, v = int(r['sub_id']), int(r['field6_raw'])
            if i in sub or v not in ids['AmoActivityData']:
                raise ValueError('invalid activity sub reference')
            sub[i] = v
        if set(sub) != ids['AmoActivitySubData']:
            raise ValueError('incomplete activity sub reference')
        return (MappingProxyType({g:tuple(v) for g,v in groups.items()}), MappingProxyType(sub))
    except (KeyError, ValueError, TypeError):
        return None


def _observe(rows, group, field, known):
    counts = Counter(); seen = defaultdict(list)
    for r in rows:
        if str(r['group_id_raw']) != str(group) or str(r['source_field']) != str(field):
            continue
        counts['rows'] += 1
        if r['value1_raw'] in (None,''):
            counts['id_absent'] += 1
            continue
        ident = int(r['value1_raw'])
        seen[ident].append(r)
    valid = set()
    for ident, records in seen.items():
        if len(records) != 1:
            counts['duplicate_ids'] += 1
        elif ident not in known:
            counts['unrecognized_ids'] += 1
        elif str(records[0]['decoded_pair']) != '1' or records[0]['value2_observation_status'] != 'binary_code_observed' or str(records[0]['value2_raw']) not in ('0','1'):
            counts['unresolved_ids'] += 1
        else:
            valid.add(ident)
            counts['code_'+str(records[0]['value2_raw'])] += 1
    return valid, dict(sorted(counts.items()))


def audit_progress_relations(bundle):
    rows = bundle.rows
    if not {'account_progress_groups.csv','account_progress_entries.csv'} <= rows.keys():
        return {'status':'snapshot_unavailable','groups':{}}
    catalog = relation_catalog()
    if catalog is None:
        return {'status':'catalog_unavailable','groups':{}}
    groups, sub = catalog
    entries = rows['account_progress_entries.csv']
    group_counts = Counter(str(r['group_id_raw']) for r in rows['account_progress_groups.csv'])
    result = {}
    for gid, kind in [(39,'control_room'),(19,'activity')]:
        if group_counts[str(gid)] != 1:
            result[kind] = {'status':'group_absent' if not group_counts[str(gid)] else 'multiple_groups'}
            continue
        if gid == 39:
            known_tasks = {t for links in groups.values() for _,t,_,_ in links}
            tasks, task_stats = _observe(entries,39,4,known_tasks)
            observed_groups, group_stats = _observe(entries,39,5,groups)
            expected = {t for g in observed_groups for _,t,_,_ in groups[g]}
            result[kind] = {'status':'candidate_membership_only', 'field4':task_stats,'field5':group_stats,
                            'expected_from_observed_groups':len(expected), 'expected_observed':len(expected & tasks),
                            'expected_not_observed_as_valid':len(expected-tasks), 'observed_outside_expected':len(tasks-expected),
                            'groups_with_all_tasks_observed':sum(all(t in tasks for _,t,_,_ in groups[g]) for g in observed_groups)}
        else:
            acts, act_stats = _observe(entries,19,4,catalogs()['AmoActivityData'])
            subs, sub_stats = _observe(entries,19,5,sub)
            result[kind] = {'status':'field6_reference_candidate_only','field4':act_stats,'field5':sub_stats,
                            'field6_target_observed':sum(sub[i] in acts for i in subs),
                            'field6_target_not_observed_as_valid':sum(sub[i] not in acts for i in subs)}
    return {'status':'static_relation_consistency_not_completion_proof','groups':result}
