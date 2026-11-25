"""Verified static item IDs for WeaponPart skins (not player-owned data).

Source: user-supplied GF2_Table_Priority.zip / kr\\ItemData.bytes.
Filter: ItemData category 配件皮肤 AND resource type WeaponPart.
The catalog permits displaying a documented item ID, not asserting that
it is currently equipped until controlled cosmetic-change evidence exists.
"""
from __future__ import annotations
import csv
from .reference_data import lru_cache
from .reference_data import files


def _positive_item_ids(filename: str) -> frozenset[int]:
    try:
        resource = files('gfl2_packet_csv.static_tables').joinpath(filename)
        with resource.open('r', encoding='utf-8-sig', newline='') as fp:
            ids = (int(row['item_id']) for row in csv.DictReader(fp))
            return frozenset(item_id for item_id in ids if item_id > 0)
    except (OSError, ValueError, KeyError, TypeError):
        # Fail closed: do not expose or label unknown raw scalar values.
        return frozenset()


@lru_cache(maxsize=1)
def known_weapon_part_skin_ids() -> frozenset[int]:
    return _positive_item_ids('weapon_part_skin_item_ids.csv')


@lru_cache(maxsize=1)
def known_weapon_skin_ids() -> frozenset[int]:
    """ItemData category 武器皮肤, resource type Weapon (not WeaponPart)."""
    return _positive_item_ids('weapon_skin_item_ids.csv')
