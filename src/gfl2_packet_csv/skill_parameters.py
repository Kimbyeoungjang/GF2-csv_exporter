"""Controlled UI observations; preserve unverified target prefix and raw code."""
COLUMNS = ('skill_prefix_code_candidate','condition_type_code_candidate','priority_code_candidate',
           'once_only_candidate','condition_type_label','priority_label','parameter_projection_status')
TYPES = {2:'default',4:'basic_stats',6:'character_element',8:'status',10:'weakness',12:'position'}
PRIORITIES = {2:{2:'default'},4:{2:'lowest_hp',4:'highest_hp'},
              6:{2:'turbid_element',12:'physical_element'},8:{2:'has_buff',8:'no_debuff'},
              10:{2:'turbid_weakness',10:'burn_weakness'},12:{2:'nearest',4:'farthest'}}
ONCE = 1 << 24

def projection(raw):
    if raw is None: return (None,)*6+('field_absent',)
    if type(raw) is not int or not 0 <= raw < 2**32: return (None,)*6+('unrecognized_pattern',)
    once = int(bool(raw & ONCE)); base = raw & ~ONCE
    prefix, rest = divmod(base, 10000); kind, priority = divmod(rest,100)
    if prefix not in (2,4,6) or kind not in TYPES or not 2 <= priority <= 98 or priority % 2:
        return (None,)*6+('unrecognized_pattern',)
    label = PRIORITIES[kind].get(priority)
    return prefix,kind,priority,once,TYPES[kind],label,'controlled_mapping_candidate' if label else 'priority_unconfirmed'

def validate(row, raw):
    expected = tuple('' if v is None else str(v) for v in projection(raw))
    if tuple(row[c] for c in COLUMNS) != expected: raise ValueError('invalid skill parameter projection')
