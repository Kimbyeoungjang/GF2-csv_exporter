"""CSV-only calibration progress. Plan maximum is not an inferred attempt count."""
from collections import Counter, defaultdict
from .reference_data import lru_cache
from types import MappingProxyType
from .attachment_affixes import _rows, validate_rows
from .bundle_import import read_bundle


@lru_cache(maxsize=1)
def calibration_plans():
    caps = {}
    for row in _rows('attachment_calibration_distribution.csv'):
        plan, units = int(row['calibration_plan_id']), int(row['value_units'])
        if plan <= 0 or not 100 <= units <= 2000 or units % 100:
            raise ValueError('invalid calibration reference')
        caps[plan] = max(caps.get(plan, 0), units)
    models = {}
    for row in _rows('attachment_calibration_model_reference.csv'):
        model = int(row['part_id'])
        plan = int(row['calibration_plan_id']) if row['calibration_plan_id'] else None
        if model <= 0 or model in models or (plan is not None and plan not in caps):
            raise ValueError('invalid calibration model')
        models[model] = plan
    if not models or not caps:
        raise ValueError('missing calibration reference')
    return MappingProxyType(models), MappingProxyType(caps)


def calibration_completion(bundle):
    if not bundle.complete_capture:
        raise ValueError('complete bundle required')
    parents = bundle.rows['attachments.csv']; options = bundle.rows['attachment_affixes.csv']
    validate_rows(parents, options)
    models, caps = calibration_plans()
    grouped = defaultdict(list)
    for row in options:
        grouped[int(row['uid'])].append(row)
    out = []
    for row in parents:
        uid, model = int(row['uid']), int(row['part_id'])
        plan = models.get(model); cap = caps.get(plan)
        status = row['calibration_status']; source = row['calibration_observation_source']
        field5 = row['field5_observation_status']
        values = [int(r['calibration_units_candidate']) for r in grouped[uid]] if status == 'structure_consistent_semantics_pending' else []
        result = 'unavailable'; maximum = None; extreme = None; at_cap = None
        if model not in models:
            result = 'unknown_model'
        elif plan is None:
            result = 'no_static_calibration_plan' if not values and field5 != 'observed_withheld' else 'reference_conflict'
        elif status == 'field_absent':
            result = 'field_absent' if field5 != 'observed_withheld' else 'reference_conflict'
        elif values:
            if any(v > cap for v in values):
                result = 'above_plan_maximum'
            else:
                at_cap = sum(v == cap for v in values)
                maximum = at_cap == len(values)
                extreme = cap == 2000 and maximum
                result = 'all_at_plan_maximum' if maximum else 'below_plan_maximum'
                if (field5 == 'observed_withheld') != extreme:
                    result = 'field5_relation_conflict'
                    # Keep numeric evidence; do not imply field5 meaning.
        out.append({'uid':uid,'part_id':model,'calibration_plan_id':plan,
                    'plan_max_units':cap,'observed_option_count':len(values),
                    'options_at_plan_maximum':at_cap,'all_at_plan_maximum_candidate':maximum,
                    'all_at_200_percent_candidate':extreme,'calibration_observation_source':source,
                    'field5_observation_status':field5,'status':result})
    return {'schema':'gf2-calibration-completion-1','parts':out,'summary':{
        'records':len(out),'statuses':dict(Counter(r['status'] for r in out)),
        'all_at_plan_maximum':sum(r['all_at_plan_maximum_candidate'] is True for r in out),
        'all_at_200_percent':sum(r['all_at_200_percent_candidate'] is True for r in out)}}


def read_calibration_completion(path):
    return calibration_completion(read_bundle(path))
