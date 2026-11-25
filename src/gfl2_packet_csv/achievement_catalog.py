"""Group 50 IDs cross-checked against the static challenge table.
State values remain raw until reward/achievement behavior is demonstrated.
"""
from __future__ import annotations
import csv
from .reference_data import lru_cache
from .reference_data import files

@lru_cache(maxsize=1)
def challenge_ids():
    try:
        with files('gfl2_packet_csv.static_achievements').joinpath('achievement_challenge_ko.csv').open('r',encoding='utf-8-sig',newline='') as f:
            rows=list(csv.DictReader(f))
        ids=frozenset(int(r['challenge_id']) for r in rows)
        return ids if len(ids)==len(rows) and all(x>0 for x in ids) else frozenset()
    except (OSError, ValueError, KeyError, TypeError):
        return frozenset()


def challenge_status(ident):
    ids=challenge_ids()
    if not ids:return 'catalog_unavailable'
    return 'catalog_consistent' if ident in ids else 'unrecognized'
