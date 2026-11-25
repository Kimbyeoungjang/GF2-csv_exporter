"""Offline complete-snapshot audits. Only counts leave the comparison.

The static catalogs are shared with the exporter; the word extraction and
candidate arithmetic below are independent of its projection functions.
Do not use for merged or partial inventory updates.
"""
from collections import Counter
from .attachment_affixes import catalogs, value_types, project_upgrade
from .key_catalog import project as key_type
from .equipment_snapshot_audit import _Audit, _keyed
from .doll_snapshot_audit import _fields, _scalar


def _words(value):
    if not 0 <= value < 2**64:
        raise ValueError('Invalid inventory uint64')
    raw = value.to_bytes(8, 'little')
    return [int.from_bytes(raw[i:i+2], 'little')
            for i in range(0, (value.bit_length()+15)//16*2, 2)]


def _options(model, packed):
    if packed is None:
        return 'field_absent', []
    if packed == 0:
        return 'explicit_zero', []
    models, rules = catalogs()
    types = value_types()
    if not models or not rules or not types:
        return 'catalog_unavailable', []
    group = models.get(model)
    if group is None:
        return 'unrecognized_model', []
    result, seen = [], set()
    for word in _words(packed):
        roll, code = word % 256, word // 256
        if not code or not roll or code in seen:
            return 'unsupported_structure', []
        seen.add(code)
        rule = rules.get((group, code))
        if rule is None:
            return 'unrecognized_affix', []
        aid, lo, hi = rule
        if not lo <= roll <= hi:
            return 'roll_range_mismatch', []
        result.append(dict(affix_group_id=group, affix_id=aid, stat_code=code,
                           roll_units=roll, value_type_code=types[aid],
                           min_roll_units=lo, max_roll_units=hi))
    return 'catalog_consistent', result


def _calibration(packed, status, count):
    if packed is None:
        return 'field_absent', []
    if packed == 0:
        return 'explicit_zero', []
    words = _words(packed)
    if status != 'catalog_consistent':
        return 'option_structure_unavailable', []
    if len(words) != count or any(w >= 8192 or w % 4096 not in range(100, 2001, 100) for w in words):
        return 'unsupported_structure', []
    return 'structure_consistent_semantics_pending', [(w % 4096, w // 4096) for w in words]


def audit_attachment_snapshot(payload, rows):
    """Compare 11061 inventory, affix rows and candidate display arithmetic."""
    top = _fields(payload)
    parts, links = _keyed(top, 1), _keyed(top, 2)
    if not parts or set(links)-set(parts):
        raise ValueError('Incomplete attachment inventory')
    if any(_scalar(r, 2) is None for r in links.values()):
        raise ValueError('Missing attachment link')
    audit = _Audit(rows, [('attachments.csv', ('uid',)), ('attachment_affixes.csv', ('uid','position'))])
    counts = Counter()
    for uid, record in parts.items():
        model, packed, level, f5, f6, cal = (_scalar(record,n) for n in range(2,8))
        if not model:
            raise ValueError('Missing attachment model')
        status, opts = _options(model, packed)
        cs, words = _calibration(cal, status, len(opts))
        plan, upgrade, us = project_upgrade(model, level)
        audit.row('attachments.csv', (uid,), dict(part_id=model, weapon_uid=_scalar(links[uid],2) if uid in links else 0,
            upgrade_plan_id=plan, upgrade_level_candidate=upgrade, upgrade_reference_status=us,
            field5_observation_status='observed_withheld' if f5 is not None else 'field_absent',
            field6_observation_status='observed_withheld' if f6 is not None else 'field_absent',
            affix_reference_status=status, affix_count=len(opts) if status in ('catalog_consistent','explicit_zero') else None,
            calibration_status=cs, calibration_observation_source='absent' if cal is None else 'observed'))
        counts['affix_rows'] += len(opts)
        counts['calibration_words'] += len(words)
        counts['field5_present'] += f5 is not None
        counts['field6_present'] += f6 is not None
        counts['unsupported_affix_records'] += status not in ('catalog_consistent','field_absent','explicit_zero')
        counts['unsupported_calibration_records'] += cs not in ('field_absent','explicit_zero','structure_consistent_semantics_pending')
        for i, opt in enumerate(opts):
            c, flag = words[i] if words else (None,None)
            flat = opt['value_type_code'] == 1
            base = opt['roll_units'] * (1000 if flat else 100)
            audit.row('attachment_affixes.csv',(uid,i+1), dict(**opt,
                calibration_units_candidate=c, calibration_flag_bit12=flag,
                stat_unit_candidate='flat' if flat else 'percentage_points', base_stat_milli_candidate=base,
                amplification_percent_candidate=None if c is None else c//10,
                amplified_stat_milli_candidate=None if c is None else base + base*c//1000))
    return {'parts':len(parts), 'links':len(links), 'observations':dict(counts), **audit.result()}


def _evolution(kind, packed):
    status, pairs = 'withheld_unrecognized', []
    if packed is None:
        status = 'field_absent'
    elif packed == 0:
        status = 'explicit_zero'
    elif kind == 2 and 0 < packed < 2**48:
        pairs = [divmod(w,256) for w in _words(packed)]
        if all(c in (9,10,29) and 1 <= s <= 10 for c,s in pairs) and len({c for c,s in pairs}) == len(pairs):
            status = 'structure_candidate'
        else:
            pairs = []
    return status, pairs


def audit_key_snapshot(payload, rows):
    """Compare 11138 inventory, candidate options and observed owner reference.

    Known doll membership uses dolls.csv, not an independent doll audit.
    Owner relationship status is intentionally outside this comparison.
    """
    keys = _keyed(_fields(payload),1)
    if not keys:
        raise ValueError('Empty key inventory is outside complete audit scope')
    owner_rows = [r for r in rows['equipment_owner_links.csv'] if r['kind']=='common_key']
    audit = _Audit({**rows,'equipment_owner_links.csv':owner_rows}, [('common_keys.csv',('uid',)),('equipment_owner_links.csv',('uid',))])
    doll_ids = {int(r['doll_id']) for r in rows['dolls.csv']}
    counts = Counter()
    for uid, record in keys.items():
        model, owner, packed = (_scalar(record,n) for n in (2,3,4))
        if not model:
            raise ValueError('Missing key model')
        kind, ks = key_type(model)
        status, pairs = _evolution(kind,packed)
        counts[status] += 1
        counts['option_words'] += len(pairs)
        values = dict(key_id=model,key_type=kind,key_type_reference_status=ks,
            evolution_observation_status=status,evolution_option_count_candidate=len(pairs) if pairs else None)
        for i in range(3):
            c,s = pairs[i] if i < len(pairs) else (None,None)
            values[f'evolution_stat_code_{i+1}']=c
            values[f'evolution_stage_candidate_{i+1}']=s
        audit.row('common_keys.csv',(uid,),values)
        audit.row('equipment_owner_links.csv',(uid,),dict(owner_field_present=int(owner is not None),
            owner_doll_id=owner if not owner or owner in doll_ids else None))
    return {'keys':len(keys),'observations':dict(counts),**audit.result()}
