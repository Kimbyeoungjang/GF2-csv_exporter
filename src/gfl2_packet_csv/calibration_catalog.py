"""Static model-specific calibration reference, not a live reroll formula.

The table exposes static weights, not probabilities conditional on history.
Never replaces account values or treats missing calibration as zero.
"""
from dataclasses import dataclass
from .reference_data import lru_cache
from types import MappingProxyType
from .attachment_affixes import _rows

@dataclass(frozen=True)
class CalibrationStep:
    value_units: int
    weight: int
    order_index: int

@dataclass(frozen=True)
class CalibrationReference:
    part_id: int
    grade_code: int
    plan_id: int | None
    steps: tuple[CalibrationStep, ...]

    @property
    def max_units(self):
        return self.steps[-1].value_units if self.steps else None

    @property
    def min_units(self):
        return self.steps[0].value_units if self.steps else None

@lru_cache(maxsize=1)
def catalog():
    """Return immutable static references or empty mapping on corrupt catalog."""
    try:
        distributions = {}
        for row in _rows('attachment_calibration_distribution.csv'):
            plan = int(row['calibration_plan_id'])
            step = CalibrationStep(*(int(row[k]) for k in ('value_units', 'weight', 'order_index')))
            distributions.setdefault(plan, []).append(step)
        if not distributions:
            raise ValueError('missing distributions')
        for plan, steps in distributions.items():
            steps.sort(key=lambda r:r.order_index)
            if (plan <= 0 or not steps or sum(s.weight for s in steps) != 100
                    or any(s.order_index != i or s.value_units != 100*i or not 0 < s.weight <= 100
                           for i,s in enumerate(steps,1))):
                raise ValueError('invalid distribution')
        result = {}
        for row in _rows('attachment_calibration_model_reference.csv'):
            part = int(row['part_id']); grade = int(row['static_grade_code'])
            plan = int(row['calibration_plan_id']) if row['calibration_plan_id'] else None
            if part <= 0 or part in result or grade <= 0 or (plan is not None and (plan not in distributions or plan//10 != grade)):
                raise ValueError('invalid model reference')
            result[part] = CalibrationReference(part,grade,plan,tuple(distributions[plan]) if plan is not None else ())
        if not result or {r.plan_id for r in result.values() if r.plan_id is not None} != set(distributions):
            raise ValueError('unreferenced distributions')
        return MappingProxyType(result)
    except (OSError, ValueError, TypeError, KeyError):
        return MappingProxyType({})


def calibration_reference(part_id):
    if type(part_id) is not int:
        return None
    return catalog().get(part_id)


def calibration_range_status(part_id, values):
    """Compare normalized, flag-free units; never infer missing observations."""
    if values is None:
        return 'field_absent'
    if not isinstance(values, (tuple,list)) or not 1 <= len(values) <= 4 or any(type(v) is not int or v <= 0 for v in values):
        return 'unsupported_values'
    if not catalog():
        return 'catalog_unavailable'
    ref = calibration_reference(part_id)
    if ref is None:
        return 'unrecognized_model'
    if ref.plan_id is None:
        return 'no_static_plan'
    domain = {s.value_units for s in ref.steps}
    if not all(v in domain for v in values):
        return 'range_mismatch'
    return 'all_at_static_maximum' if all(v == ref.max_units for v in values) else 'within_static_range'
