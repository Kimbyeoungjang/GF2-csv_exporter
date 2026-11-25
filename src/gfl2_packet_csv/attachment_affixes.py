"""Catalog-validated 11061 option words. No opaque integer output.

The observed numeric unit is retained. Candidate display arithmetic is
separate from verified wire components; UI rounding and other effects remain open.
"""
from __future__ import annotations
import csv
from .reference_data import lru_cache
from .reference_data import files

FILE = 'attachment_affixes.csv'
BASE_HEADERS = ['uid', 'position', 'affix_group_id', 'affix_id', 'stat_code', 'roll_units', 'value_type_code', 'min_roll_units', 'max_roll_units']
CAL_HEADERS = ['calibration_units_candidate', 'calibration_flag_bit12']
DISPLAY_HEADERS = ['stat_unit_candidate', 'base_stat_milli_candidate', 'amplification_percent_candidate', 'amplified_stat_milli_candidate']
HEADERS = BASE_HEADERS + CAL_HEADERS + DISPLAY_HEADERS


def _rows(name):
    with files('gfl2_packet_csv.static_tables').joinpath(name).open('r', encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


@lru_cache(maxsize=1)
def catalogs():
    try:
        model_rows = _rows('attachment_model_affix_groups.csv')
        rule_rows = _rows('attachment_affix_reference.csv')
        plan_rows = _rows('attachment_affix_plans.csv')
        plans = {int(r['affix_plan_id']): int(r['affix_group_id']) for r in plan_rows}
        models = {int(r['part_id']): int(r['affix_group_id']) for r in model_rows}
        rules = {(int(r['affix_group_id']), int(r['stat_code'])):
                 (int(r['affix_id']), int(r['min_roll_units']), int(r['max_roll_units'])) for r in rule_rows}
        if (not models or not rules or len(models) != len(model_rows) or len(rules) != len(rule_rows)
                or len(plans) != len(plan_rows) or not plans
                or any(p <= 0 or g <= 0 for p,g in plans.items())
                or any(plans.get(int(r['affix_plan_id'])) != int(r['affix_group_id']) for r in model_rows)
                or any(p <= 0 or g <= 0 for p,g in models.items())
                or any(g <= 0 or not 1 <= c <= 255 or i <= 0 or not 1 <= lo <= hi <= 255
                       for (g,c),(i,lo,hi) in rules.items())
                or not set(models.values()) <= {g for g,_ in rules}):
            return {}, {}
        return models, rules
    except (OSError, ValueError, KeyError, TypeError):
        return {}, {}


@lru_cache(maxsize=1)
def upgrade_catalogs():
    """Join model field 15 to ModUpgradeData group/level pairs."""
    try:
        model_rows = _rows('attachment_model_upgrade_plans.csv')
        level_rows = _rows('attachment_upgrade_levels.csv')
        models = {int(r['part_id']): int(r['upgrade_plan_id']) if r['upgrade_plan_id'] else None for r in model_rows}
        levels = {(int(r['upgrade_plan_id']), int(r['level'])) for r in level_rows}
        if (not models or not levels or len(models) != len(model_rows) or len(levels) != len(level_rows)
                or any(p <= 0 for p in models) or any(g <= 0 or level <= 0 for g,level in levels)
                or not {g for g in models.values() if g is not None} <= {g for g,_ in levels}):
            return {}, set()
        return models, levels
    except (OSError, ValueError, KeyError, TypeError):
        return {}, set()


def project_upgrade(part_id, observed, *, available=True):
    # A matching table supports a candidate interpretation, not a controlled upgrade proof.
    if not available:
        return None, None, 'snapshot_unavailable'
    models, levels = upgrade_catalogs()
    if not models or not levels:
        return None, None, 'catalog_unavailable'
    if part_id not in models:
        return None, None, 'unrecognized_model'
    plan = models[part_id]
    if observed is None:
        return plan, None, 'field_absent'
    if type(observed) is not int:
        return plan, None, 'unsupported_value'
    if observed == 0:
        return plan, 0, 'explicit_zero_semantics_pending'
    if plan is None:
        return None, None, 'no_model_upgrade_plan'
    if (plan, observed) not in levels:
        return plan, None, 'level_range_mismatch'
    return plan, observed, 'catalog_consistent_candidate'


@lru_cache(maxsize=1)
def value_types():
    """AffixGroupData.field4. Codes are static, not a proven display conversion."""
    try:
        rows = _rows('attachment_affix_reference.csv')
        result = {int(r['affix_id']): int(r['value_type_code']) for r in rows}
        _, rules = catalogs()
        if len(result) != len(rows) or set(result) != {i for i,_,_ in rules.values()} or any(v not in (1,2) for v in result.values()):
            return {}
        return result
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def _words(value):
    if type(value) is not int or not 0 <= value < 2**64:
        raise ValueError('invalid option uint64')
    return [(value >> shift) & 0xffff for shift in range(0, value.bit_length(), 16)]


def project_affixes(part_id, packed):
    if packed is None:
        return [], 'field_absent'
    if packed == 0:
        return [], 'explicit_zero'
    models,rules = catalogs()
    if not models or not rules or not value_types():
        return [], 'catalog_unavailable'
    group = models.get(part_id)
    if group is None:
        return [], 'unrecognized_model'
    try:
        words = _words(packed)
    except ValueError:
        return [], 'unsupported_structure'
    rows, seen = [], set()
    for pos, word in enumerate(words, 1):
        code, roll = word >> 8, word & 255
        if not code or not roll or code in seen:
            return [], 'unsupported_structure'
        seen.add(code)
        rule = rules.get((group, code))
        if rule is None:
            return [], 'unrecognized_affix'
        aid,lo,hi = rule
        if not lo <= roll <= hi:
            return [], 'roll_range_mismatch'
        rows.append({'position':pos, 'affix_group_id':group, 'affix_id':aid, 'stat_code':code, 'roll_units':roll, 'value_type_code':value_types()[aid], 'min_roll_units':lo, 'max_roll_units':hi})
    return rows, 'catalog_consistent'


def calibration_status(packed, option_count):
    if packed is None:
        return 'field_absent'
    if packed == 0:
        return 'explicit_zero'
    try:
        words = _words(packed)
    except ValueError:
        return 'unsupported_structure'
    if option_count is None:
        return 'option_structure_unavailable'
    if len(words) != option_count or any(w & 0xe000 or not 100 <= (w & 0xfff) <= 2000 or (w & 0xfff) % 100 for w in words):
        return 'unsupported_structure'
    # Only observed shape is verified; no invented bonus percentage or lock state.
    return 'structure_consistent_semantics_pending'


def display_projection(value_type, roll, calibration):
    """Candidate arithmetic, not a proof of UI rounding or all bonus effects.

    Milli-units represent 1/1000 of a flat unit or percentage point.
    Official amplification range supports calibration / 10 percent.
    Absent calibration is not zero; do not infer an amplified total.
    """
    if type(value_type) is not int or value_type not in (1, 2) or type(roll) is not int or not 1 <= roll <= 255:
        raise ValueError('invalid display projection input')
    unit = 'flat' if value_type == 1 else 'percentage_points'
    base = roll * (1000 if value_type == 1 else 100)
    if calibration is None:
        return unit, base, None, None
    if type(calibration) is not int or not 100 <= calibration <= 2000 or calibration % 100:
        raise ValueError('invalid amplification candidate')
    return unit, base, calibration // 10, base * (1000 + calibration) // 1000


def project_inventory(attachments, *, available):
    parents, rows = [], []
    counts, calibration_counts, upgrade_counts = {}, {}, {}
    calibration_sources = {}
    calibration_word_count = 0
    unresolved_counts = {'field5_present':0, 'field6_present':0, 'field5_with_calibration':0}
    for uid, item in sorted(attachments.items()):
        opts, status = project_affixes(item['part_id'], item.get('packed_stats')) if available else ([], 'snapshot_unavailable')
        cal = calibration_status(item.get('packed_calibration'), len(opts) if status == 'catalog_consistent' else None) if available else 'snapshot_unavailable'
        if available and item.get('calibration_invalidated'):
            cal = 'prior_calibration_invalidated'
        cal_source = ('snapshot_unavailable' if not available else
                      'invalidated' if cal == 'prior_calibration_invalidated' else
                      'absent' if cal == 'field_absent' else
                      'retained' if item.get('calibration_retained') else 'observed')
        calibration_sources[cal_source] = calibration_sources.get(cal_source, 0) + 1
        words = _words(item['packed_calibration']) if cal == 'structure_consistent_semantics_pending' else []
        calibration_word_count += len(words)
        plan, level, upgrade = project_upgrade(item['part_id'], item.get('level_raw'), available=available)
        if item.get('upgrade_retained') and upgrade in ('catalog_consistent_candidate','explicit_zero_semantics_pending'):
            upgrade = 'retained_' + upgrade
        upgrade_counts[upgrade] = upgrade_counts.get(upgrade, 0) + 1
        counts[status] = counts.get(status, 0) + 1
        calibration_counts[cal] = calibration_counts.get(cal, 0) + 1
        f5 = item.get('effect_id')
        f6 = item.get('state_raw')
        unresolved_counts['field5_present'] += int(available and f5 is not None)
        unresolved_counts['field6_present'] += int(available and f6 is not None)
        unresolved_counts['field5_with_calibration'] += int(available and f5 is not None and item.get('packed_calibration') is not None)
        def withheld_status(value):
            return 'snapshot_unavailable' if not available else 'observed_withheld' if value is not None else 'field_absent'
        parents.append({**{k:item.get(k) for k in ('uid','part_id','weapon_uid')},
                        'field5_observation_status':withheld_status(f5), 'field6_observation_status':withheld_status(f6),
                        'upgrade_plan_id':plan, 'upgrade_level_candidate':level, 'upgrade_reference_status':upgrade,
                        'affix_reference_status':status, 'affix_count':len(opts) if status in ('catalog_consistent','explicit_zero') else None, 'calibration_status':cal, 'calibration_observation_source':cal_source})
        rows.extend({'uid':uid, **r,
                     'calibration_units_candidate': (words[i] & 0xfff) if words else None,
                     'calibration_flag_bit12': int(bool(words[i] & 0x1000)) if words else None,
                     **dict(zip(DISPLAY_HEADERS, display_projection(r['value_type_code'], r['roll_units'], (words[i] & 0xfff) if words else None)))}
                    for i,r in enumerate(opts))
    return parents, rows, {'status':'observed' if available else 'unavailable', 'option_count':len(rows),
                            'attachment_reference_counts':counts, 'calibration_structure_counts':calibration_counts,
                            'upgrade_reference_counts':upgrade_counts, 'upgrade_level_semantics':'catalog_candidate_pending_controlled_change',
                            'affix_group_source':'WeaponModData.field11 -> AffixPlanIdData',
                            'display_value_semantics':'candidate_base_and_amplification_no_ui_rounding', 'calibration_value_semantics':'official_range_supported_candidate_percent',
                            'calibration_observation_sources':calibration_sources,
                            'calibration_component_count':calibration_word_count,
                            'unresolved_field_observations':unresolved_counts}


def validate_rows(parents, rows):
    """Validate normalized options against parents and current static catalogs.

    Accept CSV DictReader rows or typed dictionaries. Returns no private data.
    Validates candidate arithmetic, not game semantics.
    """
    def number(value):
        if type(value) is int:
            return value
        if isinstance(value,str) and value.isascii() and value.isdecimal() and len(value) <= 20:
            return int(value)
        raise ValueError('invalid affix numeric field')
    indexed = {}
    allowed = {'catalog_consistent','field_absent','explicit_zero','catalog_unavailable',
               'unrecognized_model','unsupported_structure','unrecognized_affix','roll_range_mismatch','snapshot_unavailable'}
    for p in parents:
        uid, part = number(p['uid']), number(p['part_id'])
        status = p['affix_reference_status']
        if uid <= 0 or part <= 0 or uid in indexed or status not in allowed:
            raise ValueError('invalid attachment parent')
        count = p.get('affix_count')
        if status in ('catalog_consistent','explicit_zero'):
            count = number(count)
            if not (1 <= count <= 4 if status == 'catalog_consistent' else count == 0):
                raise ValueError('invalid affix count')
        elif count not in (None, ''):
            raise ValueError('unverified affix count')
        for key in ('field5_observation_status','field6_observation_status'):
            if p[key] not in ('snapshot_unavailable','observed_withheld','field_absent'):
                raise ValueError('invalid unresolved field observation status')
            if (p[key] == 'snapshot_unavailable') != (status == 'snapshot_unavailable'):
                raise ValueError('unresolved field availability mismatch')
        cal = p['calibration_status']
        if cal not in ('field_absent','explicit_zero','unsupported_structure','option_structure_unavailable','structure_consistent_semantics_pending','prior_calibration_invalidated','snapshot_unavailable'):
            raise ValueError('invalid calibration status')
        if (cal == 'snapshot_unavailable') != (status == 'snapshot_unavailable') or (cal == 'structure_consistent_semantics_pending' and status != 'catalog_consistent'):
            raise ValueError('calibration availability mismatch')
        source = p['calibration_observation_source']
        expected_source = {'snapshot_unavailable':'snapshot_unavailable', 'prior_calibration_invalidated':'invalidated', 'field_absent':'absent'}.get(cal)
        if (source != expected_source if expected_source is not None else source not in ('observed','retained')):
            raise ValueError('calibration observation source mismatch')
        upgrade = p['upgrade_reference_status']
        plan = None if p['upgrade_plan_id'] in (None, '') else number(p['upgrade_plan_id'])
        candidate = None if p['upgrade_level_candidate'] in (None, '') else number(p['upgrade_level_candidate'])
        if upgrade in ('catalog_consistent_candidate', 'explicit_zero_semantics_pending', 'field_absent', 'retained_catalog_consistent_candidate', 'retained_explicit_zero_semantics_pending'):
            if project_upgrade(part, candidate) != (plan, candidate, upgrade.removeprefix('retained_')):
                raise ValueError('upgrade projection mismatch')
        elif upgrade in ('catalog_unavailable', 'unrecognized_model', 'snapshot_unavailable', 'no_model_upgrade_plan', 'level_range_mismatch', 'unsupported_value'):
            if candidate is not None or (upgrade in ('catalog_unavailable','unrecognized_model','snapshot_unavailable','no_model_upgrade_plan') and plan is not None):
                raise ValueError('unverified upgrade candidate')
            if upgrade in ('level_range_mismatch','unsupported_value') and plan != upgrade_catalogs()[0].get(part):
                raise ValueError('upgrade plan mismatch')
        else:
            raise ValueError('unknown upgrade reference status')
        indexed[uid] = (part,status,count,cal)
    grouped = {}
    for row in rows:
        uid = number(row['uid'])
        if uid not in indexed or indexed[uid][1] != 'catalog_consistent':
            raise ValueError('unverified option parent')
        candidate, flag = (row[k] for k in CAL_HEADERS)
        if indexed[uid][3] == 'structure_consistent_semantics_pending':
            candidate, flag = number(candidate), number(flag)
            if not 100 <= candidate <= 2000 or candidate % 100 or flag not in (0,1):
                raise ValueError('invalid calibration components')
        elif candidate not in (None,'') or flag not in (None,''):
            raise ValueError('calibration components without validated structure')
        expected_display = display_projection(number(row['value_type_code']), number(row['roll_units']), candidate if indexed[uid][3] == 'structure_consistent_semantics_pending' else None)
        for key, expected in zip(DISPLAY_HEADERS, expected_display):
            actual = row[key]
            if expected is None:
                if actual not in (None, ''):
                    raise ValueError('unexpected display candidate')
            elif isinstance(expected, str):
                if actual != expected:
                    raise ValueError('display unit mismatch')
            elif number(actual) != expected:
                raise ValueError('display candidate mismatch')
        grouped.setdefault(uid, []).append({key:number(row[key]) for key in BASE_HEADERS if key != 'uid'})
        if len(grouped[uid]) > 4:
            raise ValueError('too many affix positions')
    for uid,(part,status,count,cal) in indexed.items():
        options = sorted(grouped.get(uid, []),key=lambda x:x['position'])
        if status != 'catalog_consistent':
            continue
        if len(options) != count or [r['position'] for r in options] != list(range(1,len(options)+1)):
            raise ValueError('missing or invalid affix positions')
        if any(not 1 <= r['stat_code'] <= 255 or not 1 <= r['roll_units'] <= 255 for r in options):
            raise ValueError('invalid affix units')
        packed = sum(((r['stat_code'] << 8) | r['roll_units']) << (16*i) for i,r in enumerate(options))
        expected, actual_status = project_affixes(part,packed)
        if actual_status != status or expected != options:
            raise ValueError('affix catalog mismatch')
