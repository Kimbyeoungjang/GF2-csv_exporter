"""Import contract v3: authoritative file inventory with integrity/status metadata.

Only the ordinary private account ZIP receives the file digests. Debug never
receives this catalog or its contents. Status describes decode availability,
not full semantic verification or proof of an exhaustive inventory.
"""
from __future__ import annotations

import csv
import hashlib
import io
from pathlib import Path
from .key_evolution import FIELDS as EVOLUTION_FIELDS
from .item_catalog import FIELDS as ITEM_FIELDS
from .account_csv import HEADERS as EXT_HEADERS, SECTION_FILES
from .formation_slots import HEADERS as FORM_HEADERS
from .equipment_owner_links import HEADERS as OWNER_HEADERS
from .weapon_slot_observations import HEADERS as WEAPON_HEADERS
from . import additional_doll_details as ADD_DETAILS
from .additional_doll import FILE as ADD_DOLL_FILE, HEADERS as ADD_DOLL_HEADERS
from .dorm_costumes import FILE as DORM_FILE, HEADERS as DORM_HEADERS
from .attachment_affixes import FILE as AFFIX_FILE, HEADERS as AFFIX_HEADERS
from .progress_snapshot import GROUP_HEADERS, ENTRY_HEADERS, GROUPS_FILE, ENTRIES_FILE

CORE_HEADERS = {
    'dolls.csv': 'doll_id level rank class_stage class_stage_reference_status weapon_uid grade_record_id_candidate rank_reference_status',
    'weapons.csv': 'uid item_id level rank equipped_doll_id owner_reference_status weapon_skin_item_id weapon_skin_reference_status static_level_cap static_rank_cap level_reference_status rank_reference_status',
    'attachments.csv': 'uid part_id upgrade_plan_id upgrade_level_candidate upgrade_reference_status field5_observation_status field6_observation_status weapon_uid affix_reference_status affix_count calibration_status calibration_observation_source',
    'common_keys.csv': 'uid key_id key_type key_type_reference_status', 'items.csv': 'item_id quantity',
}
CORE_HEADERS = {k:v.split() for k,v in CORE_HEADERS.items()}
CORE_HEADERS["common_keys.csv"].extend(EVOLUTION_FIELDS)
CORE_HEADERS["items.csv"].extend(ITEM_FIELDS)
HEADERS = {**CORE_HEADERS, **EXT_HEADERS, **FORM_HEADERS, **ADD_DETAILS.HEADERS,
           'equipment_owner_links.csv':OWNER_HEADERS,
           'weapon_slot_observations.csv':WEAPON_HEADERS,
           GROUPS_FILE:list(GROUP_HEADERS), ENTRIES_FILE:list(ENTRY_HEADERS), DORM_FILE:DORM_HEADERS, ADD_DOLL_FILE:ADD_DOLL_HEADERS, AFFIX_FILE:AFFIX_HEADERS}
KEYS = {
    'dolls.csv':'doll_id', 'weapons.csv':'uid','attachments.csv':'uid','common_keys.csv':'uid','items.csv':'item_id',
    'account_status.csv':'section','doll_progress.csv':'doll_id','doll_core_slots.csv':'doll_id slot',
    'doll_key_values.csv':'doll_id kind position','doll_helix.csv':'doll_id position','doll_skills.csv':'doll_id position',
    'doll_saved_strategies.csv':'doll_id strategy_position','doll_saved_strategy_skills.csv':'doll_id strategy_position position',
    'doll_costumes.csv':'source costume_id','remolding_details.csv':'uid','remolding_options.csv':'uid position',
    'support_settings.csv':'','support_formations.csv':'position','support_units.csv':'formation_position slot_position',
    'support_unit_ammunition.csv':'formation_position slot_position position','support_attributes.csv':'formation_position attribute_id',
    'support_frames.csv':'formation_position frame_id','support_frame_slots.csv':'formation_position frame_id slot_position',
    'support_special_slots.csv':'formation_position slot_position','support_ammunition.csv':'position',
    'support_chips.csv':'kind uid','support_owned_units.csv':'position','achievements.csv':'achievement_id',
    'formation_saved_groups.csv':'formation_position','formation_slots.csv':'formation_position slot_position',
    'formation_slot_equipment_refs.csv':'formation_position slot_position source_field reference_position',
    'formation_slot_key_ids.csv':'formation_position slot_position key_kind key_position',
    'formation_slot_skill_variants.csv':'formation_position slot_position variant_position',
    'formation_slot_skills.csv':'formation_position slot_position variant_position skill_position',
    'equipment_owner_links.csv':'kind uid','weapon_slot_observations.csv':'weapon_uid wire_position',
    GROUPS_FILE:'group_position', ENTRIES_FILE:'group_position source_field entry_position', DORM_FILE:'doll_id', ADD_DOLL_FILE:'doll_id', AFFIX_FILE:'uid position',
}
KEYS = {k:v.split() for k,v in KEYS.items()}
KEYS.update(ADD_DETAILS.KEYS)
assert HEADERS.keys() == KEYS.keys()
CORE_PACKETS = {'dolls.csv':11101,
                'weapons.csv':11021,'attachments.csv':11061,'common_keys.csv':11138,'items.csv':11211}


def _csv_info(name: str, raw: bytes, *, max_rows: int | None = None):
    reader = csv.reader(io.StringIO(raw.decode('utf-8-sig'), newline=''), strict=True)
    header = next(reader, None)
    if header != HEADERS[name]:
        raise ValueError('account CSV header mismatch')
    positions = [header.index(k) for k in KEYS[name]]
    seen = set()
    count = 0
    for row in reader:
        if max_rows is not None and count >= max_rows:
            raise ValueError('account CSV row limit exceeded')
        if len(row) != len(header):
            raise ValueError('account CSV column mismatch')
        key = tuple(row[i] for i in positions)
        if key in seen or (positions and any(k == '' for k in key)):
            raise ValueError('account CSV duplicate or blank primary key')
        seen.add(key)
        count += 1
    return {'columns':header, 'primary_key':KEYS[name], 'rows':count}


def build_catalog(root: Path, members, manifest: dict) -> dict:
    names = set(members) - {'manifest.json'}
    if not names <= set(HEADERS):
        raise ValueError('unexpected account export file')
    packet_states = {p['packet_id']:p['capture_state'] for p in manifest['account_packet_coverage']['packets']}
    ext = manifest['account_extension_status']
    source_sections = {name:[section for section, ff in SECTION_FILES.items() if name in ff] for name in EXT_HEADERS}
    source_sections['doll_saved_strategies.csv'] = source_sections['doll_saved_strategy_skills.csv'] = ['dolls']
    result = {}
    for name in sorted(set(HEADERS)):
        present = name in names
        state = 'observed_projection' if present else 'not_emitted'
        if name in CORE_PACKETS:
            state = packet_states.get(CORE_PACKETS[name], 'missing')
        elif name in source_sections and source_sections[name]:
            states = [ext.get(k,'missing') for k in source_sections[name]]
            state = 'observed_projection' if all(s=='observed' for s in states) else 'partial_or_unavailable'
            if not present: state = 'not_emitted'
        if name in (GROUPS_FILE, ENTRIES_FILE): state = manifest['progress_snapshot_10833']['status']
        if name == 'weapon_slot_observations.csv': state = manifest['weapon_slot_observations']['status']
        if name in FORM_HEADERS: state = manifest['saved_formation_slots']['status']
        if name in ADD_DETAILS.HEADERS: state = manifest['additional_doll_details']['status']
        if name == ADD_DOLL_FILE: state = manifest['additional_doll_observation']['status']
        if name == DORM_FILE: state = manifest['dorm_costume_observations']['status']
        if name == AFFIX_FILE: state = manifest['attachment_affix_audit']['status']
        info = {'present':present, 'role':'account_data', 'status':state}
        if present:
            raw = (root/name).read_bytes()
            info.update({'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
            if name in HEADERS:
                info.update(_csv_info(name,raw))
        elif name in HEADERS:
            info.update({'columns':HEADERS[name], 'primary_key':KEYS[name]})
        result[name] = info
    return result
