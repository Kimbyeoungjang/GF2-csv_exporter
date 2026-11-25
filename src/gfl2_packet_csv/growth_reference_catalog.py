"""Read-only joins of existing static growth references (RC93).

No account observations, stat formula, unlock inference, or fallback stage.
LevelUpData fields 3/4/5 are deliberately unnamed raw reference values.
"""
from dataclasses import dataclass
from .reference_data import lru_cache
from types import MappingProxyType
from . import doll_growth_catalog as dolls, helix_catalog as helix, doll_promotion as promotion
from .weapon_property_catalog import level_reference


@dataclass(frozen=True)
class DollGrowthReference:
    doll_id: int
    core_model_id: int
    grade_record_ids: tuple[int, ...]
    helix_group_id: int
    helix_node_count: int
    promotion_counterpart_id: int | None
    promotion_role: str | None


@dataclass(frozen=True)
class DollStageReference:
    level_cap: int | None
    class_status: str
    grade_record_id: int | None
    rank_status: str
    level_reference_units: tuple[int, int, int] | None


@lru_cache(maxsize=1)
def catalog():
    """All-or-nothing join. Extra helix-only models are not promoted to dolls."""
    try:
        cores = dolls.core_models()
        ranks = dolls.rank_records()
        groups = helix.catalog()
        pairs = promotion.catalog()
        if (not cores or not ranks or not groups or not pairs
                or not set(cores) <= set(groups)
                or not (set(pairs) | set(pairs.values())) <= set(cores)
                or set(ranks) != {(d, r) for d in cores for r in range(7)}
                or len(set(cores.values())) != len(cores)
                or len(set(ranks.values())) != len(ranks)
                or any(min(d, c) <= 0 for d, c in cores.items())
                or any(value != d * 100 + r + 1 for (d, r), value in ranks.items())):
            return MappingProxyType({})
        reverse = {v:k for k,v in pairs.items()}
        result = {}
        for did, core in cores.items():
            group = groups[did]
            counterpart = pairs.get(did, reverse.get(did))
            role = 'base' if did in pairs else 'promoted' if did in reverse else None
            result[did] = DollGrowthReference(did, core,
                tuple(ranks[did, r] for r in range(7)), group.group_id,
                len(group.nodes), counterpart, role)
        return MappingProxyType(result)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return MappingProxyType({})


def doll_reference(doll_id):
    return catalog().get(doll_id) if type(doll_id) is int else None


def doll_reference_status(doll_id):
    if type(doll_id) is not int or doll_id <= 0:
        return 'invalid_model'
    refs = catalog()
    if not refs:
        return 'catalog_unavailable'
    if doll_id in refs:
        return 'catalog_consistent'
    return 'helix_only_reference' if doll_id in helix.catalog() else 'unknown_doll'


def stage_reference(doll_id, level, class_stage, rank):
    """Independent checks; a missing stage or rank never acquires a default.

    The level tuple is a generic LevelUpData lookup, not proof that a doll
    uses these factors. Explicit class/level mismatch withholds the cap.
    """
    model_status = doll_reference_status(doll_id)
    if model_status != 'catalog_consistent':
        return DollStageReference(None, model_status, None, model_status, None)
    valid_level = level is None or type(level) is int
    valid_stage = class_stage is None or type(class_stage) is int
    valid_rank = rank is None or type(rank) is int
    stage, class_status = (dolls.class_projection(class_stage, level)
                           if valid_level and valid_stage else (None, 'invalid_input'))
    record, rank_status = (dolls.rank_projection(doll_id, rank)
                           if valid_rank else (None, 'invalid_input'))
    cap = dolls.class_caps().get(stage) if stage is not None else None
    return DollStageReference(cap, class_status, record, rank_status, level_reference(level))


def level_factor_delta(from_level, to_level):
    """Signed raw differences, never displayed stat growth or a percentage."""
    before, after = level_reference(from_level), level_reference(to_level)
    if before is None or after is None:
        return None
    return tuple(b-a for a,b in zip(before,after))
