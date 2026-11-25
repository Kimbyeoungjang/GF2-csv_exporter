"""Offline full support snapshots: direct field and derived-total comparisons.

Returns aggregate counts only. Shared static catalogs are not independently
validated here. No inferred amplification level when the field is absent.
"""
from collections import Counter
from .doll_snapshot_audit import _fields, _scalar, _vector
from .equipment_snapshot_audit import _Audit, _keyed
from .support_catalog import (reference_status, room_level_status, frame_slots,
                              special_break_records)
from .support_attribute_totals import level_totals, secondary_values
from .remolding_catalog import primary_attributes, secondary_attribute_counts
from .support_capacity import slot_costs, chip_sizes, special_stage_skills


def audit_support_configuration(chip_payload, formation_payload, rows):
    audit = _Audit(rows, [('support_chips.csv', ('kind', 'uid')),
                          ('support_settings.csv', ()),
                          ('support_formations.csv', ('position',)),
                          ('support_attributes.csv', ('formation_position', 'attribute_id')),
                          ('support_frames.csv', ('formation_position', 'frame_id')),
                          ('support_frame_slots.csv', ('formation_position', 'frame_id', 'slot_position')),
                          ('support_special_slots.csv', ('formation_position', 'slot_position')),
                          ('support_ammunition.csv', ('position',))])
    counts = Counter()
    chip_fields = _fields(chip_payload)
    normal, special = {}, {}
    for kind, field, registry in [('normal', 1, normal), ('special', 2, special)]:
        for uid, record in _keyed(chip_fields, field).items():
            model = _scalar(record, 2)
            if not model:
                raise ValueError('Missing support chip model')
            if kind == 'normal':
                attrs = _vector(record, 4)
                if attrs is None or len(attrs) != 6:
                    raise ValueError('Expected six support chip attributes')
                stage = None
            else:
                attrs = (None,) * 6
                raw = _scalar(record, 5)
                if raw is not None and not 0 <= raw <= 5:
                    raise ValueError('Unsupported support chip stage')
                stage = None if raw is None else raw + 1
                counts['special_stage_absent' if raw is None else 'special_stage_explicit'] += 1
            registry[uid] = (model, attrs)
            counts[kind + '_chips'] += 1
            fields = dict(model_id=model, player_amplification_level=stage,
                          model_reference_status=reference_status(kind, model))
            fields.update({f'attr_{i}': value for i, value in enumerate(attrs, 1)})
            if stage is not None:
                fields['amplification_break_record_id'] = special_break_records().get((model, stage))
                group, skill = special_stage_skills().get((model, stage), (None, None))
                fields.update(amplification_skill_group_id=group, amplification_skill_id=skill)
            else:
                fields.update(amplification_break_record_id=None, amplification_skill_group_id=None, amplification_skill_id=None)
            if kind == 'normal':
                primary_refs, secondary_counts, value_refs = primary_attributes(), secondary_attribute_counts(), secondary_values()
                primary, base_value = primary_refs.get(model, (None, None))
                primary_status = ('catalog_consistent' if attrs[primary-1] == base_value else 'value_mismatch') if primary is not None else ('unrecognized' if primary_refs else 'catalog_unavailable')
                expected = secondary_counts.get(model)
                observed = sum(bool(x) for i, x in enumerate(attrs, 1) if i != primary) if expected is not None else None
                count_status = ('catalog_consistent' if expected == observed else 'count_mismatch') if expected is not None else ('unrecognized' if secondary_counts else 'catalog_unavailable')
                value_status = ('catalog_consistent' if tuple(sorted(x for i,x in enumerate(attrs,1) if i != primary and x)) == value_refs[model] else 'value_mismatch') if model in value_refs else ('unrecognized' if value_refs else 'catalog_unavailable')
                amp_status = 'not_applicable'
            else:
                primary = base_value = expected = observed = None
                primary_status = count_status = value_status = 'not_applicable'
                amp_status = ('field_absent' if stage is None else 'catalog_unavailable' if not special_break_records() else 'catalog_consistent_explicit_stage' if fields['amplification_break_record_id'] is not None else 'unrecognized_model_or_stage')
            fields.update(primary_attribute_id=primary, primary_base_value=base_value,
                          primary_reference_status=primary_status, secondary_attribute_count_expected=expected,
                          secondary_attribute_count_observed=observed, secondary_count_reference_status=count_status,
                          secondary_value_reference_status=value_status, amplification_reference_status=amp_status)
            audit.row('support_chips.csv', (kind, uid), fields)
    top = _scalar(_fields(formation_payload), 1, 2)
    if top is None:
        raise ValueError('Support configuration missing')
    base = _fields(top)
    level = _scalar(base, 2)
    audit.row('support_settings.csv', (), dict(level=level, selected_position_raw=_scalar(base, 9),
              level_reference_status=room_level_status(level)))
    for i, ammo in enumerate(_vector(base, 8) or (), 1):
        audit.row('support_ammunition.csv', (i,), dict(ammunition_id=ammo,
                  reference_status=reference_status('support_ammunition', ammo)))
        counts['ammunition_positions'] += 1
    static_level = level_totals().get(level)
    slots, costs, sizes = frame_slots(), slot_costs(), chip_sizes()
    for pos, (wire, raw) in enumerate(base.get(1, ()), 1):
        if wire != 2:
            raise ValueError('Invalid formation wire type')
        formation = _fields(raw)
        counts['formations'] += 1
        special_slots = _vector(formation, 6)
        for i, uid in enumerate(special_slots or (), 1):
            counts['special_slots'] += 1
            counts['occupied_special_slots'] += bool(uid)
            counts['unresolved_special_slots'] += bool(uid and uid not in special)
            audit.row('support_special_slots.csv', (pos, i), dict(uid=uid or None))
        used = _scalar(formation, 4)
        refs = []
        capacity = 0 if costs and sizes else None
        for fid, frame in _keyed(formation, 5).items():
            counts['frames'] += 1
            audit.row('support_frames.csv', (pos, fid), dict(frame_id=fid))
            inner = _scalar(frame, 2, 2)
            if inner is None:
                raise ValueError('Missing frame slot message')
            fields = _fields(inner)
            vals = _vector(fields, 1)
            if set(fields) != {1} or vals is None or len(vals) != 7:
                raise ValueError('Expected seven frame slots')
            for i, uid in enumerate(vals, 1):
                sid = slots.get((fid, i))
                audit.row('support_frame_slots.csv', (pos, fid, i), dict(uid=uid or None, static_slot_id=sid))
                counts['normal_slots'] += 1
                if not uid:
                    continue
                counts['occupied_normal_slots'] += 1
                chip, cost = normal.get(uid), costs.get(sid)
                counts['unresolved_normal_slots'] += chip is None
                if capacity is not None:
                    if uid in refs or chip is None or cost is None or sizes.get(chip[0]) != cost[0]:
                        capacity = None
                    else:
                        capacity += cost[1]
                refs.append(uid)
        max_capacity = static_level[6] if static_level else None
        if not costs or not sizes: capacity_status = 'catalog_unavailable'
        elif capacity is None: capacity_status = 'slot_reference_unavailable'
        elif used is None: capacity_status = 'field_absent'
        elif max_capacity is None: capacity_status = 'level_unavailable'
        elif used == max_capacity == capacity: capacity_status = 'matches_both'
        elif used == max_capacity: capacity_status = 'matches_level_limit'
        elif used == capacity: capacity_status = 'matches_occupied_slots'
        else: capacity_status = 'value_mismatch'
        audit.row('support_formations.csv', (pos,), dict(used_capacity=used,
                  special_slots_present=int(special_slots is not None),
                  level_capacity_candidate=max_capacity, occupied_slot_capacity_candidate=capacity,
                  capacity_comparison_status=capacity_status))
        if used is not None and capacity is not None:
            counts['capacity_matches' if used == capacity else 'capacity_mismatches'] += 1
        for attr, record in _keyed(formation, 3).items():
            counts['attribute_rows'] += 1
            value = _scalar(record, 2)
            fields = dict(value=value)
            if 1 <= attr <= 6 and static_level is not None:
                initial = static_level[attr - 1]
                total = None if len(set(refs)) != len(refs) or any(uid not in normal for uid in refs) else sum(normal[uid][1][attr - 1] for uid in refs)
                fields.update(level_base_candidate=initial, normal_chip_sum=total,
                              attribute_sum_status='chip_reference_unavailable' if total is None else 'field_absent' if value is None else 'catalog_consistent_candidate' if value == initial + total else 'value_mismatch')
                if value is not None and total is not None:
                    counts['attribute_matches' if value == initial + total else 'attribute_mismatches'] += 1
            else:
                fields.update(level_base_candidate=None, normal_chip_sum=None,
                              attribute_sum_status='unrecognized_attribute' if not 1 <= attr <= 6 else 'level_unavailable')
            audit.row('support_attributes.csv', (pos, attr), fields)
    if not counts['formations']:
        raise ValueError('No support formations')
    return {'observations': dict(counts), **audit.result()}
