"""Read-only GF2Tools account bundle preflight. No extraction or game access.

Integrity is not authentication. A complete packet coverage flag does not
mean every game field was decoded or that inventory ownership is exhaustive.
"""
from __future__ import annotations
import csv, hashlib, io, json, zipfile, os, zlib, lzma
from collections import defaultdict, Counter
from dataclasses import dataclass, field
from .dataset_catalog import HEADERS, KEYS, CORE_HEADERS, CORE_PACKETS, _csv_info
from .account_csv import OPTIONAL_FILES, SECTION_FILES, CSV_SCHEMA, deserialize
from .support_integrity import build_support_audit
from .account_extension import SCHEMA, VERSION, TYPES
from .key_catalog import project as key_projection, audit as key_audit
from .key_evolution import validate_row as validate_evolution_row
from .item_catalog import validate_row as validate_item_row
from .account_export_scope import DEFAULT_EXPORT_TYPES, CORE_ACCOUNT_TYPES, account_packet_coverage
from .equipment_owner_links import FILE as OWNER_FILE
from .attachment_affixes import validate_rows
from .progress_snapshot import validate_projection, GROUPS_FILE, ENTRIES_FILE, PROGRESS_FILES
from .achievement_catalog import challenge_status
from .formation_slots import FILES as FORM_FILES, REF_FIELDS, _catalogs, saved_key_projection, saved_key_audit

from .doll_growth_catalog import rank_projection
from .weapon_growth_catalog import growth_projection
from .weapon_skin_catalog import known_weapon_skin_ids, known_weapon_part_skin_ids
from .weapon_slot_observations import FILE as SLOT_FILE

from .additional_doll import FILE as ADD_DOLL_FILE, validate_growth as validate_additional_growth, weapon_status as additional_weapon_status

from . import additional_doll_details as ADD_DETAILS
from . import doll_promotion
from . import dorm_costumes
from .skill_strategy_order import movement_projection

from .skill_parameters import validate as validate_param

CONTRACT = 35
MAX_MEMBER = 24 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
MAX_ROWS = 200_000
# Bound the compressed input as well as the expanded member inventory.
MAX_ARCHIVE = 80 * 1024 * 1024

@dataclass
class AccountBundle:
    manifest: dict
    rows: dict[str, list[dict[str, str]]]
    extension: dict
    complete_capture: bool
    archive_sha256: str = ""
    embedded_states: dict[str, bytes] = field(default_factory=dict)
    state_binding_sha256: str = ""


def archive_snapshot(source):
    """Read a bounded immutable copy; the digest and ZIP rows use identical bytes."""
    if isinstance(source, (str, bytes, os.PathLike)):
        with open(source, "rb") as handle:
            data = handle.read(MAX_ARCHIVE + 1)
    else:
        source.seek(0)
        data = source.read(MAX_ARCHIVE + 1)
    if not isinstance(data, bytes) or len(data) > MAX_ARCHIVE:
        raise ValueError("archive input size or type rejected")
    return data


def bound_archive_digest(bundle_path, bundle):
    """Reject a changed path even if the new ZIP has the same inventory IDs."""
    current = hashlib.sha256(archive_snapshot(bundle_path)).hexdigest()
    expected = getattr(bundle, "archive_sha256", "")
    if expected and current != expected:
        raise ValueError("bundle snapshot mismatch")
    return expected or current


def _require(ok, message):
    if not ok:
        raise ValueError(message)


def _object(pairs):
    out = {}
    for key, value in pairs:
        _require(key not in out, 'duplicate JSON member')
        out[key] = value
    return out


def _reject_constant(_value):
    raise ValueError("nonfinite JSON constant")


def _uint(v, *, optional=False, positive=False):
    if optional and v == '': return None
    _require(isinstance(v,str) and v.isascii() and v.isdecimal() and len(v)<=20, 'invalid unsigned integer')
    n=int(v)
    _require(str(n)==v and int(positive)<=n<2**64, 'noncanonical or out-of-range integer')
    return n


def _sequence(rows, field):
    _require(sorted(_uint(r[field],positive=True) for r in rows)==list(range(1,len(rows)+1)), 'invalid positional sequence')


def _indexed(rows, fields):
    index={}
    for r in rows:
        k=tuple(_uint(r[f],positive=True) for f in fields)
        _require(k not in index, 'duplicate numeric key')
        index[k]=r
    return index


def _same_json(a, b):
    # Python treats True == 1; JSON metadata must preserve types.
    return json.dumps(a, sort_keys=True, allow_nan=False) == json.dumps(b, sort_keys=True, allow_nan=False)


def _validate_coverage(manifest):
    coverage = manifest['account_packet_coverage']
    packets = coverage['packets']
    ids = [p['packet_id'] for p in packets]
    _require(all(type(n) is int for n in ids) and len(ids)==len(set(ids))
             and set(ids)==DEFAULT_EXPORT_TYPES, 'invalid packet coverage inventory')
    def counts(name):
        data = manifest[name]
        _require(isinstance(data, dict) and all(k in {str(n) for n in DEFAULT_EXPORT_TYPES}
                 and type(v) is int and v >= 0 for k,v in data.items()), 'invalid packet counters')
        return {int(k):v for k,v in data.items()}
    received, errors = counts('payload_counts'), counts('decode_errors')
    success = {p['packet_id'] for p in packets if p['core_decoder']=='observed'}
    _require(success <= CORE_ACCOUNT_TYPES and all(received.get(n,0)>0 for n in success),
             'invalid core decoder observation')
    _require(all(not count or received.get(n,0)>0 for n,count in errors.items()), 'decode error without received packet')
    expected = account_packet_coverage(received,errors,success,manifest['account_extension_status'],
        progress_snapshot_status=manifest['progress_snapshot_10833']['status'])
    _require(_same_json(coverage,expected), 'inconsistent packet coverage')
    return packets, expected['all_12_decoded_without_error']


def _validate_extension_metadata(manifest, extension, extfiles, catalog, packets):
    """Cross-check only evidence reconstructible from the exported CSVs."""
    failed = manifest['account_extension_export_error']
    _require(type(failed) is bool, 'invalid extension export error flag')
    _require(not failed or not extfiles, 'failed extension export has CSVs')
    if extfiles:
        _require(type(manifest['account_csv_schema']) is int and
                 manifest['account_csv_schema'] == CSV_SCHEMA, 'extension CSV schema metadata mismatch')
    else:
        _require('account_csv_schema' not in manifest, 'unexpected extension CSV schema metadata')
    for packet in packets:
        section = TYPES.get(packet['packet_id'])
        if section and extension['status'][section] != 'missing':
            _require(packet['received_packets'] > 0, 'extension status without source packet')
    sources = {name: [section for section, files in SECTION_FILES.items() if name in files]
               for name in OPTIONAL_FILES}
    for name in ('doll_saved_strategies.csv', 'doll_saved_strategy_skills.csv'):
        sources[name] = ['dolls']
    for name, sections in sources.items():
        expected = ('not_emitted' if name not in extfiles else
                    'observed_projection' if all(extension['status'][s] == 'observed' for s in sections)
                    else 'partial_or_unavailable')
        _require(catalog[name]['status'] == expected, 'extension dataset status mismatch')
    expected_audit = ({'schema_version': 1, 'status': 'audit_unavailable'} if failed
                      else build_support_audit(extension))
    _require(_same_json(manifest['support_data_audit'], expected_audit), 'support audit mismatch')


def _validate_owner_row(row, current, owned, doll_ready):
    """Verify only implications recoverable from exported evidence, not hidden raw values."""
    present = row['owner_field_present']
    _require(present in ('0','1'), 'invalid owner field presence')
    did = _uint(row['owner_doll_id'],optional=True)
    st = row['reference_status']
    _require(present=='1' or did is None, 'owner value without field')
    if st == 'invalid_owner_field':
        valid = present=='1' and did is None
    elif not doll_ready:
        valid = st=='doll_snapshot_unavailable' and did in (None,0)
    elif st == 'unmatched_doll':
        # The exporter intentionally withholds the unmatched positive ID.
        valid = present=='1' and did is None
    elif st in ('detached','slot_only'):
        valid = (did==0 if present=='1' else did is None) and st==('slot_only' if current else 'detached')
    elif st in ('verified','slot_mismatch','owner_only'):
        valid = present=='1' and did in owned and did != 0 and st==(
            'verified' if current=={did} else 'slot_mismatch' if current else 'owner_only')
    else:
        valid = False
    _require(valid, 'inconsistent equipment owner projection')


def _validate_formations(rows, inventories, common):
    present=set(FORM_FILES)&rows.keys()
    if not present: return
    _require(present==set(FORM_FILES), 'incomplete saved formation tables')
    groups=rows['formation_saved_groups.csv']; _sequence(groups,'formation_position')
    group_index=_indexed(groups,('formation_position',))
    slots=_indexed(rows['formation_slots.csv'],('formation_position','slot_position'))
    by_group=defaultdict(list)
    refs=defaultdict(list); keys=defaultdict(list); variants=defaultdict(list); skills=defaultdict(list)
    keycat,skillcat,corecat=_catalogs()
    def ref(row, value, status, kind):
        st=row[status]; uid=_uint(row[value],optional=True,positive=True)
        _require(st in {'verified','unavailable','unmatched','empty','not_observed'}, 'invalid formation reference status')
        _require((uid is not None)==(st=='verified'), 'invalid formation reference projection')
        if uid is not None: _require(uid in inventories[kind], 'missing verified formation reference')
    for (g,s),row in slots.items():
        _require((g,) in group_index and row['formation_name']==group_index[g,]['formation_name'],'invalid formation group link')
        by_group[g].append(row); ref(row,'weapon_uid','weapon_reference_status','weapon')
        for f in ('core_present','core_slots_present','fixed_keys_present','expansion_keys_present','skill_setting_present'):
            _require(row[f] in ('0','1'),'invalid formation presence flag')
        model=_uint(row['core_model_id'],optional=True,positive=True)
        st=row['core_model_status']; _require(st in {'verified','unmatched','not_observed'},'invalid core status')
        _require((model is not None)==(st=='verified') and (model is None or model in corecat),'invalid saved core model')
        if row['core_present']=='0': _require(row['core_slots_present']=='0' and row['core_level']=='' and st=='not_observed','core without presence')
        if row['skill_setting_present']=='0': _require(row['skill_mode_code']=='','skill mode without presence')
        mode = _uint(row['skill_mode_code'], optional=True)
        projected = (_uint(row['movement_directive_code_candidate'], optional=True), row['movement_directive_status'])
        _require(projected == movement_projection(mode), 'invalid saved movement directive')
    for (g,),row in group_index.items():
        _sequence(by_group[g],'slot_position'); _require(len(by_group[g])==_uint(row['slot_count']),'formation slot count mismatch')
    for row in rows['formation_slot_equipment_refs.csv']:
        k=tuple(_uint(row[f],positive=True) for f in ('formation_position','slot_position')); _require(k in slots,'orphan formation equipment reference')
        field=row['source_field']; kind=row['reference_kind']; _require({str(f):v for f,v in REF_FIELDS.items()}.get(field)==kind,'invalid formation reference kind')
        ref(row,'uid','reference_status',kind)
        uid=_uint(row['uid'],optional=True,positive=True)
        key_id=_uint(common[(uid,)]['key_id'],positive=True) if kind=='common_key' and uid is not None else None
        expected=saved_key_projection(field,row['reference_status'],key_id)
        _require(tuple(row[f] for f in ('key_role','key_model_id','key_type','key_type_reference_status'))==tuple('' if v is None else str(v) for v in expected),'invalid saved key type projection')
        refs[k,field].append(row)
    for k,slot in slots.items():
        core=refs[k,'7.2']; _require(len(core)==(6 if slot['core_slots_present']=='1' else 0),'invalid saved core slots')
        for field in ('3','6','7.2','9'): _sequence(refs[k,field],'reference_position')
        for field,col in (('3','attachment_ref_count'),('6','common_key_ref_count'),('9','bond_key_ref_count')):
            count = _uint(slot[col],optional=True)
            _require(len(refs[k,field])==(count or 0),'saved equipment reference count mismatch')
    for row in rows['formation_slot_key_ids.csv']:
        k=tuple(_uint(row[f],positive=True) for f in ('formation_position','slot_position')); _require(k in slots,'orphan formation key')
        kind=row['key_kind']; _require(kind in ('fixed_key','expansion_key'),'invalid saved key kind')
        _require(slots[k]['fixed_keys_present' if kind=='fixed_key' else 'expansion_keys_present']=='1','key without presence')
        v=_uint(row['key_id'],optional=True,positive=True); st=row['reference_status']
        _require(st in ('verified','unmatched','empty') and (v is not None)==(st=='verified'),'invalid saved key status')
        if v is not None: _require(keycat.get(v)==(1 if kind=='fixed_key' else 3,_uint(slots[k]['doll_id'])),'invalid saved key catalog reference')
        keys[k,kind].append(row)
    for group in keys.values(): _sequence(group,'key_position')
    for k,slot in slots.items():
        for kind,presence,col in (('fixed_key','fixed_keys_present','fixed_key_count'),('expansion_key','expansion_keys_present','expansion_key_count')):
            count=_uint(slot[col],optional=True)
            _require((count is not None)==(slot[presence]=='1') and len(keys[k,kind])==(count or 0),'saved key count mismatch')
    vi=_indexed(rows['formation_slot_skill_variants.csv'],('formation_position','slot_position','variant_position'))
    for k,r in vi.items():
        _require(k[:2] in slots and slots[k[:2]]['skill_setting_present']=='1','orphan saved skill variant')
        mode=_uint(slots[k[:2]]['skill_mode_code'],optional=True); sub=_uint(r['submode_code'],optional=True)
        _require(r['active']==('' if mode is None else str(int((mode&255)==sub))),'invalid active variant')
        variants[k[:2]].append(r)
    for group in variants.values(): _sequence(group,'variant_position')
    for k,slot in slots.items():
        count=_uint(slot['skill_variant_count'],optional=True)
        _require((count is not None)==(slot['skill_setting_present']=='1') and len(variants[k])==(count or 0),'saved strategy count mismatch')
    for r in rows['formation_slot_skills.csv']:
        k=tuple(_uint(r[f],positive=True) for f in ('formation_position','slot_position','variant_position')); _require(k in vi,'orphan saved skill')
        v=_uint(r['skill_id'],optional=True,positive=True); st=r['reference_status']
        _require(st in ('verified','unmatched') and (v is not None)==(st=='verified') and (v is None or v in skillcat),'invalid saved skill reference')
        if v is None: _require(r['parameter_code']=='','unverified skill parameter')
        validate_param(r, _uint(r['parameter_code'], optional=True))
        skills[k].append(r)
    for k,r in vi.items():
        _sequence(skills[k],'skill_position'); _require(len(skills[k])==_uint(r['skill_count']),'saved skill count mismatch')



def _validate_weapon_cosmetics(rows, manifest, catalog, weapons, attachments, packets):
    """Validate only released skin/slot evidence, never infer suppressed IDs."""
    skin_states = ('catalog_verified', 'retained_catalog_verified', 'explicit_zero',
                   'field_absent', 'field_unobserved', 'unrecognized')
    counts = dict.fromkeys(skin_states, 0)
    known = known_weapon_skin_ids()
    for row in weapons.values():
        st = row['weapon_skin_reference_status']
        skin = _uint(row['weapon_skin_item_id'], optional=True, positive=True)
        _require(st in counts and ((skin in known) if st in skin_states[:2] else skin is None),
                 'invalid weapon skin projection')
        counts[st] += 1
    _require(_same_json(counts, manifest['weapon_skin_reference_counts']), 'weapon skin summary mismatch')
    meta = manifest['weapon_slot_observations']
    state = meta['status']
    valid_states = {'not_observed', 'weapon_snapshot_unavailable', 'attachment_snapshot_unavailable',
                    'unsupported_slot_structure', 'link_mismatch', 'validated_observed_links'}
    present = SLOT_FILE in rows
    _require(state in valid_states and catalog[SLOT_FILE]['status'] == state and
             present == (state == 'validated_observed_links') and
             meta['file'] == (SLOT_FILE if present else None) and
             type(meta['schema_version']) is int and meta['schema_version'] == 4,
             'invalid weapon slot availability')
    if not present:
        _require('csv_sha256' not in meta, 'unexpected weapon slot digest')
        return
    _require(all(any(p['packet_id'] == typ and p['core_decoder'] == 'observed' and
                     p['decode_errors'] == 0 for p in packets) for typ in (11021, 11061)),
             'weapon slot snapshots unavailable')
    _require(meta['csv_sha256'] == catalog[SLOT_FILE]['sha256'], 'weapon slot digest mismatch')
    used, grouped = set(), defaultdict(list)
    skin_counts = Counter()
    part_skins = known_weapon_part_skin_ids()
    for row in rows[SLOT_FILE]:
        w = _uint(row['weapon_uid'], positive=True)
        a = _uint(row['attachment_uid'], optional=True)
        _require((w,) in weapons, 'orphan weapon slot')
        grouped[w].append(row)
        if a:
            _require((a,) in attachments and a not in used and
                     _uint(attachments[a,]['weapon_uid'], optional=True) == w, 'invalid weapon slot link')
            used.add(a)
        skin = _uint(row['attachment_skin_item_id'], optional=True, positive=True)
        st = row['attachment_skin_reference_status']
        _require(st in ('field_absent', 'explicit_zero', 'catalog_verified', 'unrecognized') and
                 ((skin in part_skins) if st == 'catalog_verified' else skin is None),
                 'invalid part skin projection')
        skin_counts[st] += 1
    _require(bool(grouped), 'empty observed weapon slot projection')
    for group in grouped.values():
        _require(len(group) <= 8, 'too many weapon slot positions')
        _sequence(group, 'wire_position')
    _require(used == {a[0] for a,r in attachments.items() if _uint(r['weapon_uid'],optional=True)},
             'incomplete equipped attachment links')
    verified = skin_counts['catalog_verified']
    observed = len(rows[SLOT_FILE]) - skin_counts['field_absent']
    expected = {
        'weapons_with_field8': len(grouped), 'weapons_with_supported_structure': len(grouped),
        'unsupported_weapons': 0, 'observed_positions': len(rows[SLOT_FILE]),
        'nonempty_attachment_refs': len(used), 'validated_attachment_references': len(used),
        'unknown_attachment_references': 0, 'conflicting_attachment_references': 0,
        'duplicate_attachment_references': 0, 'unknown_weapon_references': 0,
        'unmatched_equipped_attachments': 0, 'secondary_values': 0,
        'observed_skin_item_ids': observed, 'catalog_verified_skin_item_ids': verified,
        'unrecognized_skin_values_suppressed': observed - verified,
        'skin_catalog_total_ids': len(part_skins), 'suppressed_unverified_secondary_values': True,
        'skin_semantic_status': ('static_item_category_part_skin_verified' if observed and observed == verified else
                                 'partly_verified_non_catalog_values_suppressed' if verified else 'unverified_or_unobserved'),
        'csv_release_policy': 'release_only_if_all_observed_links_valid',
        'semantic_status': 'attachment_uid_verified_secondary_unknown',
    }
    _require(all(_same_json(meta[k], v) for k,v in expected.items()), 'weapon slot summary mismatch')

def _validate_affix_metadata(rows, manifest, catalog, packets):
    parents = rows['attachments.csv']
    options = rows.get('attachment_affixes.csv', [])
    ready = any(p['packet_id'] == 11061 and p['core_decoder'] == 'observed'
                and p['decode_errors'] == 0 for p in packets)
    status = 'observed' if ready else 'unavailable'
    _require(('attachment_affixes.csv' in rows) == ready and
             catalog['attachment_affixes.csv']['status'] == status,
             'affix dataset availability mismatch')
    _require(all((r['affix_reference_status'] == 'snapshot_unavailable') == (not ready)
                 and (r['upgrade_reference_status'] == 'snapshot_unavailable') == (not ready)
                 for r in parents), 'affix snapshot availability mismatch')
    def counts(field):
        return dict(Counter(r[field] for r in parents))
    expected = {
        'status': status, 'option_count': len(options),
        'attachment_reference_counts': counts('affix_reference_status'),
        'calibration_structure_counts': counts('calibration_status'),
        'upgrade_reference_counts': counts('upgrade_reference_status'),
        'upgrade_level_semantics': 'catalog_candidate_pending_controlled_change',
        'affix_group_source': 'WeaponModData.field11 -> AffixPlanIdData',
        'display_value_semantics': 'candidate_base_and_amplification_no_ui_rounding',
        'calibration_value_semantics': 'official_range_supported_candidate_percent',
        'calibration_observation_sources': counts('calibration_observation_source'),
        'calibration_component_count': sum(r['calibration_units_candidate'] != '' for r in options),
        'unresolved_field_observations': {
            'field5_present': sum(r['field5_observation_status'] == 'observed_withheld' for r in parents),
            'field6_present': sum(r['field6_observation_status'] == 'observed_withheld' for r in parents),
            'field5_with_calibration': sum(r['field5_observation_status'] == 'observed_withheld' and
                r['calibration_observation_source'] in ('observed', 'retained') for r in parents),
        },
    }
    _require(_same_json(manifest['attachment_affix_audit'], expected), 'affix audit mismatch')


def _validate_progress_metadata(rows, manifest, catalog, packets):
    meta = manifest['progress_snapshot_10833']
    status = meta['status']
    packet = next(p for p in packets if p['packet_id'] == 10833)
    _require(status in ('missing', 'invalid', 'conflict', 'observed') and
             (status == 'missing') == (packet['received_packets'] == 0),
             'progress source status mismatch')
    present = set(PROGRESS_FILES) & rows.keys()
    _require(present == (set(PROGRESS_FILES) if status == 'observed' else set()) and
             all(catalog[n]['status'] == status for n in PROGRESS_FILES),
             'progress dataset availability mismatch')
    if status != 'observed':
        _require(_same_json(meta, {'schema_version': 1, 'status': status,
                 'files': [], 'semantic_status': 'not_interpreted'}), 'progress audit mismatch')
        return
    groups, entries = rows[GROUPS_FILE], rows[ENTRIES_FILE]
    refs = defaultdict(Counter)
    for r in entries:
        if r['reference_catalog_candidate']:
            refs[f"{r['group_id_raw']}.{r['source_field']}"][r['reference_status']] += 1
    state = [r for r in entries if r['group_id_raw'] == '50' and r['source_field'] == '4']
    extra = [r for r in entries if r['group_id_raw'] == '50' and r['source_field'] == '6']
    a = {_uint(r['value1_raw']) for r in state if r['value1_raw'] != ''}
    b = {_uint(r['value1_raw']) for r in extra if r['value1_raw'] != ''}
    withheld = sum(r['value2_observation_status'] == 'withheld_group50_additional' for r in extra)
    # Timestamp-shaped counts depend on private withheld values. Only the
    # mathematically possible range can be verified, not the actual count.
    timestamp_count = meta['challenge_audit']['additional_timestamp_shaped_count']
    _require(type(timestamp_count) is int and 0 <= timestamp_count <= withheld,
             'progress withheld count out of bounds')
    expected = {
        'schema_version': 5, 'status': 'observed', 'group_count': len(groups),
        'entry_count': len(entries), 'group50_field4_count': len(state),
        'wire_sha256': None, 'files': list(PROGRESS_FILES),
        'unparsed_field_occurrences': sum(_uint(r['other_fields_count']) for r in groups) +
            sum(r['decoded_pair'] == '0' for r in entries),
        'semantic_status': 'group50_challenge_ids_verified_state_codes_and_other_groups_unconfirmed',
        'challenge_audit': {
            'state_rows': len(state), 'additional_rows': len(extra),
            'challenge_catalog_matches': sum(challenge_status(_uint(r['value1_raw'], optional=True)) == 'catalog_consistent' for r in state),
            'additional_catalog_matches': sum(challenge_status(_uint(r['value1_raw'], optional=True)) == 'catalog_consistent' for r in extra),
            'unique_keys_match': bool(state and extra) and len(a) == len(state) and len(b) == len(extra) and a == b,
            'additional_timestamp_shaped_count': timestamp_count,
            'additional_values_withheld': withheld,
            'state_semantics': 'unconfirmed', 'additional_semantics': 'unconfirmed',
        },
        'reference_candidate_audit': {k: dict(v) for k, v in refs.items()},
        'value2_observation_counts': dict(Counter(r['value2_observation_status'] for r in entries)),
        'csv_sha256': {n: catalog[n]['sha256'] for n in PROGRESS_FILES},
    }
    _require(_same_json(meta, expected), 'progress audit mismatch')


def _validate_achievement_progress_join(rows):
    """Both files are projections of the same group50 field4 records.

    Partial captures can independently fail either decoder. Compare only when
    both projections exist, and preserve absent state instead of defaulting.
    """
    if not {'achievements.csv', GROUPS_FILE, ENTRIES_FILE} <= rows.keys():
        return
    group50 = [r for r in rows[GROUPS_FILE] if r['group_id_raw'] == '50']
    _require(len(group50) == 1, 'achievement progress group mismatch')
    projected = {}
    for r in rows[ENTRIES_FILE]:
        if r['group_id_raw'] != '50' or r['source_field'] != '4':
            continue
        ident = _uint(r['value1_raw'], positive=True)
        state = _uint(r['value2_raw'], optional=True)
        # Extension decoder tolerates extra fields but never invalid state fields.
        _require(r['value2_observation_status'] in ('binary_code_observed', 'field_absent')
                 and ident not in projected, 'achievement progress record mismatch')
        projected[ident] = state
    actual = {_uint(r['achievement_id'], positive=True): _uint(r['state_raw'], optional=True)
              for r in rows['achievements.csv']}
    _require(len(actual) == len(rows['achievements.csv']) and actual == projected,
             'achievement progress projection mismatch')


def read_bundle(source, *, allow_partial=False):
    """Return validated in-memory rows. Never write files or silently import a partial capture."""
    try:
        snapshot = archive_snapshot(source)
        archive_sha256 = hashlib.sha256(snapshot).hexdigest()
        with zipfile.ZipFile(io.BytesIO(snapshot)) as z:
            infos=z.infolist(); names=[i.filename for i in infos]
            from .embedded_states import STATE_MEMBERS, read_embedded_states
            _require(len(names)==len(set(names)) and set(names)<=set(HEADERS)|{'manifest.json'}|STATE_MEMBERS,'unexpected or duplicate ZIP member')
            _require('manifest.json' in names and set(CORE_HEADERS)<=set(names),'missing mandatory bundle files')
            _require(all(not i.is_dir() and not i.flag_bits&1 and i.file_size<=MAX_MEMBER for i in infos) and sum(i.file_size for i in infos)<=MAX_TOTAL,'bundle size or ZIP flags rejected')
            manifest_bytes=z.read('manifest.json')
            manifest=json.loads(manifest_bytes.decode('utf-8'),object_pairs_hook=_object,parse_constant=_reject_constant)
            from .reference_data import require_manifest
            require_manifest(manifest)
            embedded, state_binding = read_embedded_states(z, manifest_bytes, set(names))
            _require(manifest['schema_id']=='gfl2-user-csv-backup' and type(manifest['schema_version']) is int and manifest['schema_version']==1 and type(manifest['account_data_contract']) is int and manifest['account_data_contract']==CONTRACT,'unsupported account contract')
            catalog=manifest['datasets']; _require(set(catalog)==set(HEADERS),'invalid dataset inventory')
            _require(all(type(d['present']) is bool for d in catalog.values()),'invalid dataset presence flag')
            _require({n for n,d in catalog.items() if d['present']}==set(names)-{'manifest.json'}-STATE_MEMBERS,'manifest/file inventory mismatch')
            raw={}; rows={}
            for n,d in catalog.items():
                _require(d['columns']==HEADERS[n] and d['primary_key']==KEYS[n] and d['role']=='account_data','dataset schema mismatch')
                if not d['present']: continue
                _require(type(d['bytes']) is int and 0 <= d['bytes'] <= MAX_MEMBER and
                         type(d['rows']) is int and 0 <= d['rows'] <= MAX_ROWS, 'invalid dataset size metadata')
                b=z.read(n); _require(len(b)==d['bytes'] and hashlib.sha256(b).hexdigest()==d['sha256'],'dataset digest mismatch')
                info=_csv_info(n,b,max_rows=MAX_ROWS); _require(info['rows']==d['rows'] and info['rows']<=MAX_ROWS,'dataset row count mismatch')
                raw[n]=b; rows[n]=list(csv.DictReader(io.StringIO(b.decode('utf-8-sig'),newline='')))
        packets, complete = _validate_coverage(manifest)
        _require(allow_partial or complete,'partial capture requires explicit permission')
        extfiles={n:b for n,b in raw.items() if n in OPTIONAL_FILES}
        _require(set(manifest.get('account_csv_files',[]))==set(extfiles),'extension file inventory mismatch')
        dolls=_indexed(rows['dolls.csv'],('doll_id',)); weapons=_indexed(rows['weapons.csv'],('uid',)); attachments=_indexed(rows['attachments.csv'],('uid',)); common=_indexed(rows['common_keys.csv'],('uid',)); items=_indexed(rows['items.csv'],('item_id',)); remolds=_indexed(rows.get('remolding_details.csv',[]),('uid',))
        inventories={k:{x[0] for x in index} for k,index in [('weapon',weapons),('attachment',attachments),('common_key',common),('remolding',remolds)]}
        for index,name in [(dolls,'dolls'),(weapons,'weapons'),(attachments,'attachments'),(common,'common_keys'),(items,'items')]: _require(type(manifest['counts'][name]) is int and manifest['counts'][name]==len(index),'core inventory count mismatch')
        for row in rows['dolls.csv']:
            record, status = rank_projection(_uint(row['doll_id'],positive=True), _uint(row['rank'],optional=True))
            _require(row['grade_record_id_candidate']==('' if record is None else str(record)) and row['rank_reference_status']==status,'invalid rank projection')
        for row in rows['items.csv']:
            _uint(row['quantity']); validate_item_row(row)
        for row in rows['weapons.csv']:
            projection = growth_projection(_uint(row['item_id'],positive=True), _uint(row['level'],optional=True), _uint(row['rank'],optional=True))
            for field, value in zip(('static_level_cap','static_rank_cap','level_reference_status','rank_reference_status'), projection):
                _require(row[field]==('' if value is None else str(value)), 'invalid weapon growth projection')
        for row in rows['common_keys.csv']:
            kind, status=key_projection(_uint(row['key_id'],positive=True))
            _require(row['key_type']==('' if kind is None else str(kind)) and row['key_type_reference_status']==status,'invalid key type projection')
            validate_evolution_row(row)
        extension=deserialize(extfiles,doll_ids={k[0] for k in dolls},remolding_uids=inventories['remolding']) if extfiles else {'schema_id':SCHEMA,'schema_version':VERSION,'sections':{},'status':{k:'missing' for k in TYPES.values()}}
        _require(_same_json(key_audit(extension,{k[0]:{'key_id':int(r['key_id'])} for k,r in common.items()},inventory_available=next(p['core_decoder']=='observed' and p['decode_errors']==0 for p in packets if p['packet_id']==11138)),manifest['talent_key_reference_audit']),'key reference audit mismatch')
        _require(extension['status']==manifest['account_extension_status'],'extension status mismatch')
        _validate_extension_metadata(manifest, extension, extfiles, catalog, packets)
        for name,packet in CORE_PACKETS.items():
            _require(catalog[name]['status']==next(p['capture_state'] for p in packets if p['packet_id']==packet),'core dataset status mismatch')
        if complete:
            _require(all(s=='observed' for s in extension['status'].values()),'complete coverage lacks observed extension')
            _require(set(FORM_FILES)|{'attachment_affixes.csv','account_progress_groups.csv','account_progress_entries.csv','equipment_owner_links.csv'} <= rows.keys(),'complete coverage lacks required projection')
        validate_rows(rows['attachments.csv'],rows.get('attachment_affixes.csv',[]))
        progress={'account_progress_groups.csv','account_progress_entries.csv'}
        _require(not progress&rows.keys() or progress<=rows.keys(),'incomplete progress tables')
        if progress<=rows.keys(): validate_projection(rows['account_progress_groups.csv'],rows['account_progress_entries.csv'])
        for fileset,status in [(set(FORM_FILES),manifest['saved_formation_slots']['status']), (progress,manifest['progress_snapshot_10833']['status']), ({'attachment_affixes.csv'},manifest['attachment_affix_audit']['status'])]:
            _require((fileset<=rows.keys())==(status=='observed'),'dataset availability mismatch')
        _validate_affix_metadata(rows, manifest, catalog, packets)
        _validate_progress_metadata(rows, manifest, catalog, packets)
        _validate_achievement_progress_join(rows)
        _validate_formations(rows,inventories,common)
        if set(FORM_FILES)<=rows.keys():
            _require(manifest['saved_formation_slots']['schema_version']==6 and saved_key_audit(rows['formation_slot_equipment_refs.csv'])==manifest['saved_formation_slots']['saved_key_type_audit'],'saved key audit mismatch')
            fm=manifest['saved_formation_slots']; slots=rows['formation_slots.csv']
            expected={'formations':len(rows['formation_saved_groups.csv']), 'slots':len(slots), 'occupied_slots':sum(bool(r['doll_id']) for r in slots), 'core_records':sum(r['core_present']=='1' for r in slots), 'skill_settings':sum(r['skill_setting_present']=='1' for r in slots), 'skill_variants':len(rows['formation_slot_skill_variants.csv']), 'skill_entries':len(rows['formation_slot_skills.csv'])}
            _require(all(type(fm[k]) is int and fm[k]==v for k,v in expected.items()),'saved formation summary mismatch')
        claims=defaultdict(set)
        for doll in extension['sections'].get('dolls',{}).get('rows',{}).values():
            did=doll['doll_id']
            for f in ('common_key_uids_raw','bond_key_uids_raw'):
                for uid in doll.get(f) or []:
                    if uid: claims['common_key',int(uid)].add(did)
            for uid in (doll.get('core') or {}).get('remolding_slots') or []:
                if uid: claims['remolding',int(uid)].add(did)
        owner_meta=manifest['current_equipment_owner_links']
        observed_kinds=[kind for packet,kind in ((11138,'common_key'),(11163,'remolding')) if any(p['packet_id']==packet and p['core_decoder']=='observed' and p['decode_errors']==0 for p in packets)]
        expected_owners={(k,uid) for k in observed_kinds for uid in inventories[k]}
        owner_rows=rows.get('equipment_owner_links.csv',[])
        _require(('equipment_owner_links.csv' in rows)==bool(observed_kinds) and owner_meta['observed_kinds']==observed_kinds and owner_meta['rows']==len(owner_rows) and owner_meta['status_counts']==dict(Counter(r['reference_status'] for r in owner_rows)), 'owner projection inventory mismatch')
        _require({(r['kind'],_uint(r['uid'],positive=True)) for r in owner_rows}==expected_owners and len(owner_rows)==len(expected_owners),'missing or duplicate equipment owner projection')
        doll_ready = any(p['packet_id']==11101 and p['core_decoder']=='observed' and p['decode_errors']==0 for p in packets) and 'dolls' in extension['sections']
        _require(owner_meta['schema_version']==1 and owner_meta['source_fields']==['11138.3','11163.4']
                 and owner_meta['doll_snapshot_available'] is doll_ready
                 and owner_meta['file']==(OWNER_FILE if observed_kinds else None), 'owner projection metadata mismatch')
        owned = {did for (did,) in dolls}
        for row in owner_rows:
            k=row['kind']; uid=_uint(row['uid'],positive=True)
            _validate_owner_row(row,claims[k,uid],owned,doll_ready)
        doll_weapon_claims=defaultdict(set)
        for (did,),row in dolls.items():
            uid=_uint(row['weapon_uid'],optional=True)
            if uid: doll_weapon_claims[uid].add(did)
        dorm_meta=manifest['dorm_costume_observations']
        _require(catalog[dorm_costumes.FILE]['status']==dorm_meta['status'],'dorm dataset status mismatch')
        dorm_costumes.validate_projection(rows.get(dorm_costumes.FILE, []), dorm_meta, {did for (did,) in dolls}, present=dorm_costumes.FILE in rows, doll_available=any(p['packet_id']==11101 and p['core_decoder']=='observed' and p['decode_errors']==0 for p in packets), dorm_received=any(p['packet_id']==11627 and p['received_packets']>0 for p in packets), uint=_uint)
        extra_meta=manifest['additional_doll_observation']
        extra_rows=rows.get(ADD_DOLL_FILE, [])
        state=extra_meta['status']
        _require(state in {'missing','not_observed','observed','invalid','conflict','doll_snapshot_unavailable','overlaps_owned_doll'},'invalid additional doll observation state')
        _require(catalog[ADD_DOLL_FILE]['status']==state and extra_meta['semantic_status']=='separate_record_promotion_link_candidate_not_active_form_proof' and extra_meta['rows']==len(extra_rows) and len(extra_rows)==int(state=='observed') and (ADD_DOLL_FILE in rows)==(state=='observed') and extra_meta['file']==(ADD_DOLL_FILE if extra_rows else None),'additional doll projection mismatch')
        if extra_rows:
            row=extra_rows[0]
            numeric={k:_uint(v,optional=k!='doll_id',positive=k=='doll_id') for k,v in row.items() if k not in ('weapon_reference_status','promotion_reference_status','promotion_active_reference_status')}
            _require((numeric['doll_id'],) not in dolls,'additional doll overlaps owned inventory')
            validate_additional_growth(numeric)
            doll_promotion.validate({**numeric, 'promotion_reference_status':row['promotion_reference_status']}, {did for (did,) in dolls})
            _require(doll_promotion.active_projection({**numeric, 'promotion_reference_status':row['promotion_reference_status']}, {did for (did,) in dolls}) == (numeric['promotion_active_doll_id_candidate'],row['promotion_active_reference_status']),'promotion active projection mismatch')
            available=all(any(p['packet_id']==typ and p['core_decoder']=='observed' and p['decode_errors']==0 for p in packets) for typ in (11101,11021))
            _require(any(p['packet_id']==11101 and p['core_decoder']=='observed' and p['decode_errors']==0 for p in packets),'additional doll without observed packet')
            indexed_weapons={uid:{'equipped_doll_id':_uint(w['equipped_doll_id'],optional=True)} for (uid,),w in weapons.items()}
            _require(row['weapon_reference_status']==additional_weapon_status(numeric,indexed_weapons,doll_weapon_claims,available),'additional doll weapon reference mismatch')
        detail_meta=manifest['additional_doll_details']; detail_state=detail_meta['status']
        _require(detail_state in {'parent_unavailable','not_observed','invalid','conflict','reference_unverified','observed'},'invalid additional details status')
        _require((detail_state=='parent_unavailable')==(not bool(extra_rows)),'additional detail parent mismatch')
        _require(all(catalog[n]['status']==detail_state for n in ADD_DETAILS.HEADERS),'additional detail catalog mismatch')
        present=set(ADD_DETAILS.HEADERS)&rows.keys()
        _require(present==(set(ADD_DETAILS.HEADERS) if detail_state=='observed' else set()),'incomplete additional detail files')
        if detail_state=='observed':
            common_index={uid:{'key_id':_uint(r['key_id'],positive=True)} for (uid,),r in common.items()}
            common_available=any(p['packet_id']==11138 and p['core_decoder']=='observed' and p['decode_errors']==0 for p in packets)
            ADD_DETAILS.validate(numeric['doll_id'],{n:rows[n] for n in present},detail_meta['projection'],common_index,common_available,_uint)
        else: _require(detail_meta['projection'] is None,'unexpected additional detail projection')
        both_ready = all(any(p['packet_id']==typ and p['core_decoder']=='observed' and p['decode_errors']==0 for p in packets) for typ in (11101,11021))
        for (uid,),row in weapons.items():
            did=_uint(row['equipped_doll_id'],optional=True)
            current=doll_weapon_claims[uid]
            expected=('snapshot_unavailable' if not both_ready else
                      'owner_outside_owned_dolls' if did and did not in owned else
                      'verified' if did and current=={did} else
                      'reference_mismatch' if did or current else 'no_link_observed')
            _require(row['owner_reference_status']==expected,'inconsistent weapon owner projection')
        _require(manifest['weapon_owner_reference_counts']==dict(Counter(r['owner_reference_status'] for r in weapons.values())), 'weapon owner summary mismatch')
        _validate_weapon_cosmetics(rows, manifest, catalog, weapons, attachments, packets)
        return AccountBundle(manifest,rows,extension,complete,archive_sha256,embedded,state_binding)
    except (KeyError,TypeError,AttributeError,UnicodeError,csv.Error,zipfile.BadZipFile,RuntimeError,OverflowError,zlib.error,lzma.LZMAError,EOFError,IndexError) as exc:
        raise ValueError('invalid account bundle structure') from exc
