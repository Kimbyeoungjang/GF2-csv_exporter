"""Static reference candidates for observed 10833 counter group/field pairs.

A matching ID demonstrates catalog membership, not a verified counter meaning.
The entire static ID sets are packaged, never account-selected subsets.
"""
from __future__ import annotations
import csv
from .reference_data import lru_cache
from .reference_data import files

CANDIDATES = {
    (9, 4): 'WeeklyQuestData',
    (10, 4): 'AchievementDetailData',
    (19, 4): 'AmoActivityData',
    (19, 5): 'AmoActivitySubData',
    (33, 4): 'ActivityMainTaskData',
    (39, 4): 'ControlRoomTaskData',
    (39, 5): 'ControlRoomTaskByGroupData',
    (42, 4): 'RichmanTaskData',
    (50, 4): 'AchievementChallengeData',
    (50, 6): 'AchievementChallengeData',
    (58, 4): 'Kittycafe2DialogueData',
}

@lru_cache(maxsize=1)
def catalogs():
    try:
        expected = set(CANDIDATES.values())
        result = {name:set() for name in expected}
        with files('gfl2_packet_csv.static_tables').joinpath('progress_reference_ids.csv').open('r',encoding='utf-8-sig',newline='') as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != ['catalog', 'reference_id']:
                return {}
            for row in reader:
                name, ident = row['catalog'], int(row['reference_id'])
                if name not in expected or ident <= 0 or ident in result[name]:
                    return {}
                result[name].add(ident)
        return {k:frozenset(v) for k,v in result.items()} if all(result.values()) else {}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def reference_projection(group_id, source_field, ident):
    name = CANDIDATES.get((group_id, source_field))
    if name is None:
        return None, 'no_catalog_candidate'
    if ident is None:
        return name, 'field_absent'
    refs = catalogs().get(name)
    if not refs:
        return name, 'catalog_unavailable'
    return name, 'catalog_consistent_candidate' if ident in refs else 'unrecognized'
