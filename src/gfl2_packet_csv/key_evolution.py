"""Bounded candidate projection for 11138.1.4, not a confirmed stat formula."""
from .key_catalog import project

CODES = frozenset((9, 10, 29))  # observed only; do not extrapolate other codes
FIELDS = ('evolution_observation_status', 'evolution_option_count_candidate', *(
    name for i in range(1, 4) for name in (f'evolution_stat_code_{i}', f'evolution_stage_candidate_{i}')))

def projection(key_id, value):
    empty = (None,) * 7
    if value is None:
        return ('field_absent', *empty)
    if value == 0:
        return ('explicit_zero', *empty)
    if project(key_id)[0] != 2 or not isinstance(value, int) or not 0 < value < 1 << 48:
        return ('withheld_unrecognized', *empty)
    options = []
    while value:
        word = value & 65535
        code, stage = word >> 8, word & 255
        if code not in CODES or not 1 <= stage <= 10 or code in {p[0] for p in options}:
            return ('withheld_unrecognized', *empty)
        options.append((code, stage))
        value >>= 16
    return ('structure_candidate', len(options), *(x for pair in options for x in pair), *([None] * (6-2*len(options))))

def validate_row(row):
    status = row[FIELDS[0]]
    values = tuple(row[f] for f in FIELDS[1:])
    if status in ('field_absent', 'explicit_zero', 'withheld_unrecognized'):
        if any(values): raise ValueError('unexpected key evolution data')
        return
    if status != 'structure_candidate' or values[0] not in ('1','2','3'):
        raise ValueError('invalid key evolution status/count')
    count = int(values[0]); packed = 0
    for i in range(count):
        c, s = values[1+2*i:3+2*i]
        if c not in ('9','10','29') or s not in tuple(str(n) for n in range(1,11)):
            raise ValueError('invalid key evolution component')
        packed |= (int(c)*256+int(s)) << (16*i)
    expected = tuple('' if v is None else str(v) for v in projection(int(row['key_id']),packed))
    if tuple(row[f] for f in FIELDS) != expected:
        raise ValueError('invalid key evolution projection')
