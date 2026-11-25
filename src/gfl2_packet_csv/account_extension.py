"""GF2 account extension v1; offline, stdlib only, no network or raw-file logging.
Copies belong in each repository package. Raw labels are deliberately not UI labels.
"""
from __future__ import annotations
import json
from collections import defaultdict
from .wire_probe import _varint as vint, parse_fields
from dataclasses import dataclass, field
NAME = 'account_extension.json'
SCHEMA = 'gf2-account-extension'
VERSION = 2
MAX_BYTES = 16 * 1024 * 1024
TYPES = {11101: 'dolls', 11163: 'remoldings', 22044: 'support_formations', 22062: 'support_chips', 22058: 'support_unit_ids', 10833: 'achievement_states', 11627: 'wardrobe_11627'}
META_KEY = 'account_extension_v1'

def parse(b):
    # Keep one checked wire parser for core, extension and diagnostics.
    # Extension consumers expect fixed fields as integers rather than bytes.
    out = defaultdict(list)
    for f,w,x in parse_fields(b, max_fields=100000, max_bytes=MAX_BYTES):
        out[f].append((w, int.from_bytes(x,'little') if w in (1,5) else x))
    return dict(out)

def scalar(d, k, default=None):
    xs = d.get(k, [])
    if not xs:
        return default
    if len(xs) != 1 or xs[0][0] != 0:
        raise ValueError('expected scalar')
    return xs[0][1]

def bytes1(d, k):
    xs = d.get(k, [])
    if len(xs) != 1 or xs[0][0] != 2:
        raise ValueError('expected message')
    return xs[0][1]

def records(d, k):
    xs = d.get(k, [])
    if any((w != 2 for w, _ in xs)):
        raise ValueError('expected repeated messages')
    return [parse(x) for _, x in xs]

def msg(d, k):
    return parse(bytes1(d, k)) if k in d else {}

def nums(d, k):
    out = []
    for w, x in d.get(k, []):
        if w == 0:
            out.append(x)
        elif w == 2:
            i = 0
            while i < len(x):
                n, i = vint(x, i)
                out.append(n)
        else:
            raise ValueError('expected packed integers')
    return out

def ident(d, k):
    v = scalar(d, k)
    if not isinstance(v, int) or v <= 0:
        raise ValueError('invalid reference id')
    return v

def keyed(rows, key):
    out = {}
    for row in rows:
        k = str(row[key])
        if k in out:
            raise ValueError('duplicate key')
        out[k] = row
    return out

def uidvec(d, k):
    return [str(n) if n else None for n in nums(d, k)] if k in d else None

def wardrobe(d):
    return keyed([{'costume_id': ident(r, 1), 'state_raw': scalar(r, 2)} for r in records(d, 2)], 'costume_id')

def decode_frame(r):
    # 22044.1.1.5.2 contains a nested message, not a packed 9-int vector.
    # Its tag and byte length are framing metadata, not account fields.
    inner = msg(r, 2)
    if set(inner) != {1}:
        raise ValueError('unexpected support frame slot message')
    values = nums(inner, 1)
    if len(values) != 7:
        raise ValueError('expected seven support frame slots')
    return {'frame_id': ident(r, 1), 'slots': [str(n) if n else None for n in values]}

def decode(typ, data):
    d = parse(data)
    if typ == 11101:
        result = []
        for r in records(d, 1):
            a = msg(r, 50)
            c = msg(r, 55)
            row = {'doll_id': ident(r, 1), 'costume_id': scalar(r, 5), 'costume_state_raw': scalar(r, 54), 'affinity': {'level': scalar(a, 1), 'cumulative_xp': scalar(a, 2)} if 50 in r else None, 'core': {'level': scalar(c, 1), 'model_id': scalar(c, 3), 'remolding_slots': uidvec(c, 2)} if 55 in r else None, 'fixed_key_ids_raw': nums(r, 42) if 42 in r else None, 'common_key_ids_raw': nums(r, 43) if 43 in r else None, 'common_key_uids_raw': uidvec(r, 45), 'helix_values_raw': nums(r, 51) if 51 in r else None, 'expansion_key_ids_raw': nums(r, 52) if 52 in r else None, 'expansion_stage_raw': scalar(r, 58), 'bond_key_ids_raw': nums(r, 59) if 59 in r else None, 'bond_key_uids_raw': uidvec(r, 60), 'skill_setting_raw': None}
            if 57 in r:
                s = msg(r, 57)
                # The game may send several saved strategies for one doll.
                # Select the active variant by the low byte of composite mode;
                # never drop the entire doll snapshot just because inactive
                # variants are also present (Hafsi 0x0103: variants 2 and 3).
                variants = records(s, 2)
                if len(variants) > 16:
                    raise ValueError('too many stored skill variants')
                if len(variants) == 1:
                    inner = variants[0]
                elif variants:
                    active_id = scalar(s, 1)
                    if active_id is None:
                        raise ValueError('active skill strategy is missing')
                    selected = [v for v in variants if scalar(v, 1) == (active_id & 255)]
                    if len(selected) != 1:
                        raise ValueError('ambiguous active skill strategy')
                    inner = selected[0]
                else:
                    inner = {}
                def decoded_variant(variant):
                    block = msg(variant, 2)
                    return {'submode_raw': scalar(variant, 1),
                            'entries': [{'skill_id': ident(x, 1), 'parameter_raw': scalar(x, 2)}
                                        for x in records(block, 1)]}

                active_variant = decoded_variant(inner)
                row['skill_setting_raw'] = {'mode_raw': scalar(s, 1), **active_variant,
                                            'variant_count': len(variants),
                                            'active_variant_position': next((i for i, v in enumerate(variants, 1) if v is inner), None)}
                if len(variants) > 1:
                    # Retain inactive strategies that the game stores alongside
                    # the active strategy. Previously they were silently lost.
                    # The CSV sidecar is optional, preserving legacy 31-file
                    # captures and the stable account CSV schema version.
                    row['skill_setting_raw']['saved_variants_raw'] = [
                        decoded_variant(v) for v in variants if v is not inner]
            result.append(row)
        if not result:
            raise ValueError('empty doll snapshot not established')
        return {'rows': keyed(result, 'doll_id'), 'wardrobe_11101': wardrobe(d)}
    if typ == 11163:
        return keyed([{'uid': str(ident(r, 3)), 'model_id': ident(r, 1), 'option_ids': nums(r, 2)} for r in records(d, 1)], 'uid')
    if typ == 11627:
        return wardrobe(d)
    if typ == 22062:
        normal = []
        special = []
        for r in records(d, 1):
            values = nums(r, 4)
            if len(values) != 6:
                raise ValueError('expected six chip attributes')
            normal.append({'uid': str(ident(r, 1)), 'model_id': ident(r, 2), 'attributes': values})
        for r in records(d, 2):
            # Observed 22062.special field 5: absent before amplification,
            # raw=1 at UI Lv.2 for model 2211503. The R7 stage table
            # is zero-indexed (stage 1 -> +2.7% shown at Lv.2).
            # Absence remains unknown rather than fabricating Lv.1.
            stage_raw = scalar(r, 5)
            if stage_raw is not None and not 0 <= stage_raw <= 5:
                raise ValueError('unsupported special chip stage')
            special.append({'uid': str(ident(r, 1)), 'model_id': ident(r, 2),
                            'current_player_amplification_level': stage_raw + 1 if stage_raw is not None else None})
        return {'normal': keyed(normal, 'uid'), 'special': keyed(special, 'uid')}
    if typ == 22044:
        b = msg(d, 1)
        forms = []
        for i, r in enumerate(records(b, 1), 1):
            units = [{'unit_id': scalar(x, 1), 'skill_id': scalar(x, 2), 'ammunition_ids': nums(x, 3)} if x else None for x in records(r, 2)]
            attrs = keyed([{'attribute_id': ident(x, 1), 'value': scalar(x, 2)} for x in records(r, 3)], 'attribute_id')
            frames = keyed([decode_frame(x) for x in records(r, 5)], 'frame_id')
            forms.append({'position': i, 'units': units, 'attributes': attrs, 'used_capacity': scalar(r, 4), 'frames': frames, 'special_slots': uidvec(r, 6)})
        if not forms:
            raise ValueError('no support formations')
        return {'level': scalar(b, 2), 'selected_position_raw': scalar(b, 9), 'ammunition_ids': nums(b, 8), 'formations': forms}
    if typ == 22058:
        # The wire packet is an ordered list. Sorting/deduplicating changes
        # the captured sequence (both RC50 and RC53 are not sorted). Preserve
        # every position exactly; interpretation of this ordering is unknown.
        return nums(d, 1)
    if typ == 10833:
        groups = [r for r in records(d, 1) if scalar(r, 1) == 50]
        if len(groups) != 1:
            raise ValueError('no unique achievement group50')
        rows = keyed([{'achievement_id': ident(r, 1), 'state_raw': scalar(r, 2)} for r in records(groups[0], 4)], 'achievement_id')
        if any((r['state_raw'] is not None and r['state_raw'] not in (0, 1) for r in rows.values())):
            raise ValueError('unknown achievement state')
        return {'rows': rows, 'counted_state1': sum((r['state_raw'] == 1 for r in rows.values()))}
    raise ValueError('unsupported type')

@dataclass
class ExtensionCollector:
    chunks: dict = field(default_factory=dict)
    status: dict = field(default_factory=dict)

    def observe(self, typ, data):
        if typ not in TYPES:
            return
        k = TYPES[typ]
        if self.status.get(k) in ('conflict', 'invalid'):
            return
        try:
            p = decode(typ, bytes(data))
            validate_document({'schema_id': SCHEMA, 'schema_version': VERSION, 'sections': {k: p}, 'status': {name: 'observed' if name == k else 'missing' for name in TYPES.values()}})
            if k in self.chunks and self.chunks[k] != p:
                self.chunks.pop(k, None)
                self.status[k] = 'conflict'
                return
            self.chunks[k] = p
            self.status[k] = 'observed'
        except (ValueError, TypeError, KeyError, IndexError, OverflowError):
            self.chunks.pop(k, None)
            self.status[k] = 'invalid'

    def document(self, *, doll_ids=None, remolding_uids=None):
        sections = dict(self.chunks)
        status = {k: self.status.get(k, 'missing') for k in TYPES.values()}
        for k, expected, actual in [('dolls', doll_ids, set(sections.get('dolls', {}).get('rows', {}))), ('remoldings', remolding_uids, set(sections.get('remoldings', {})))]:
            if expected is not None and k in sections and (set(map(str, expected)) != actual):
                sections.pop(k)
                status[k] = 'core_mismatch'
        if 'dolls' in sections and 'remoldings' in sections:
            ids = set(sections['remoldings'])
            if any((u not in ids for r in sections['dolls']['rows'].values() for u in (r.get('core') or {}).get('remolding_slots') or [] if u)):
                sections.pop('dolls')
                status['dolls'] = 'reference_mismatch'
        if 'support_formations' in sections and 'support_chips' in sections:
            ns = set(sections['support_chips']['normal'])
            ss = set(sections['support_chips']['special'])
            if any((u not in ns for r in sections['support_formations']['formations'] for frame in r['frames'].values() for u in frame['slots'] or [] if u)) or any((u not in ss for r in sections['support_formations']['formations'] for u in r['special_slots'] or [] if u)):
                sections.pop('support_formations')
                status['support_formations'] = 'reference_mismatch'
        return {'schema_id': SCHEMA, 'schema_version': VERSION, 'sections': sections, 'status': status}

def _obj(v, fields):
    if not isinstance(v, dict) or set(v) != set(fields.split()):
        raise ValueError('계정 확장 객체 필드 불일치')
    return v

def _int(v, nullable=False, positive=False):
    if v is None and nullable:
        return
    if type(v) is not int or not int(positive) <= v < 2 ** 64:
        raise ValueError('계정 확장 정수 범위 오류')

def _uid(v, nullable=False):
    if v is None and nullable:
        return
    if not isinstance(v, str) or not v.isascii() or (not v.isdecimal()) or (len(v) > 20) or (not 0 < int(v) < 2 ** 64) or (str(int(v)) != v):
        raise ValueError('계정 확장 UID 오류')

def _arr(v, fn, nullable=False, length=None):
    if v is None and nullable:
        return
    if not isinstance(v, list) or len(v) > 100000 or (length is not None and len(v) != length):
        raise ValueError('계정 확장 배열 오류')
    for x in v:
        fn(x)

def _map(v, fn, id_field):
    if not isinstance(v, dict) or len(v) > 100000:
        raise ValueError('계정 확장 목록 오류')
    for k, r in v.items():
        fn(r)
        if k != str(r[id_field]):
            raise ValueError('계정 확장 키/ID 불일치')

def _state(r):
    _obj(r, 'costume_id state_raw')
    _int(r['costume_id'], positive=True)
    _int(r['state_raw'], nullable=True)

def _doll(r):
    _obj(r, 'doll_id costume_id costume_state_raw affinity core fixed_key_ids_raw common_key_ids_raw common_key_uids_raw helix_values_raw expansion_key_ids_raw expansion_stage_raw bond_key_ids_raw bond_key_uids_raw skill_setting_raw')
    _int(r['doll_id'], positive=True)
    for k in ('costume_id', 'costume_state_raw', 'expansion_stage_raw'):
        _int(r[k], nullable=True)
    for k in ('fixed_key_ids_raw', 'common_key_ids_raw', 'helix_values_raw', 'expansion_key_ids_raw', 'bond_key_ids_raw'):
        _arr(r[k], _int, nullable=True)
    for k in ('common_key_uids_raw', 'bond_key_uids_raw'):
        _arr(r[k], lambda x: _uid(x, True), nullable=True)
    a = r['affinity']
    if a is not None:
        _obj(a, 'level cumulative_xp')
        _int(a['level'], nullable=True)
        _int(a['cumulative_xp'], nullable=True)
    c = r['core']
    if c is not None:
        _obj(c, 'level model_id remolding_slots')
        _int(c['level'], nullable=True)
        _int(c['model_id'], nullable=True)
        _arr(c['remolding_slots'], lambda x: _uid(x, True), nullable=True, length=6)
    s = r['skill_setting_raw']
    if s is not None:
        allowed = {'mode_raw', 'submode_raw', 'entries', 'variant_count', 'active_variant_position'}
        if set(s) not in (allowed, allowed | {'saved_variants_raw'}):
            raise ValueError('스킬 설정 필드 불일치')
        _int(s['mode_raw'], True)
        _int(s['submode_raw'], True)

        def entry(x):
            _obj(x, 'skill_id parameter_raw')
            _int(x['skill_id'], positive=True)
            _int(x['parameter_raw'], True)
        _arr(s['entries'], entry)
        count, active = s['variant_count'], s['active_variant_position']
        _int(count)
        if count > 16 or count != (1 + len(s.get('saved_variants_raw', [])) if active is not None else 0):
            raise ValueError('invalid strategy variant count')
        if count == 0:
            if active is not None or s['submode_raw'] is not None or s['entries'] or 'saved_variants_raw' in s:
                raise ValueError('unexpected empty strategy fields')
        elif type(active) is not int or not 1 <= active <= count:
            raise ValueError('invalid active strategy position')
        if count > 1 and (s['mode_raw'] is None or s['submode_raw'] != (s['mode_raw'] & 255)):
            raise ValueError('active strategy mode mismatch')
        if 'saved_variants_raw' in s:
            variants = s['saved_variants_raw']
            if not isinstance(variants, list) or not 1 <= len(variants) <= 15:
                raise ValueError('비활성 전략 개수 오류')
            seen = {s['submode_raw']}
            for variant in variants:
                _obj(variant, 'submode_raw entries')
                _int(variant['submode_raw'], positive=True)
                if variant['submode_raw'] in seen:
                    raise ValueError('중복 전략 ID')
                seen.add(variant['submode_raw'])
                _arr(variant['entries'], entry)

def _remolding(r):
    _obj(r, 'uid model_id option_ids')
    _uid(r['uid'])
    _int(r['model_id'], positive=True)
    _arr(r['option_ids'], _int)

def _chip(r, special=False):
    _obj(r, 'uid model_id ' + ('current_player_amplification_level' if special else 'attributes'))
    _uid(r['uid'])
    _int(r['model_id'], positive=True)
    if special:
        level = r['current_player_amplification_level']
        if level is not None and (type(level) is not int or not 1 <= level <= 6):
            raise ValueError('특수 소자 증폭 단계 범위 오류')
    else:
        _arr(r['attributes'], _int, length=6)

def _form(r):
    _obj(r, 'position units attributes used_capacity frames special_slots')
    _int(r['position'], positive=True)
    _int(r['used_capacity'], True)

    def unit(x):
        if x is None:
            return
        _obj(x, 'unit_id skill_id ammunition_ids')
        _int(x['unit_id'], True)
        _int(x['skill_id'], True)
        _arr(x['ammunition_ids'], _int)

    def attr(x):
        _obj(x, 'attribute_id value')
        _int(x['attribute_id'], positive=True)
        _int(x['value'], True)

    def frame(x):
        _obj(x, 'frame_id slots')
        _int(x['frame_id'], positive=True)
        _arr(x['slots'], lambda x: _uid(x, True), length=7)
    _arr(r['units'], unit)
    _map(r['attributes'], attr, 'attribute_id')
    _map(r['frames'], frame, 'frame_id')
    _arr(r['special_slots'], lambda x: _uid(x, True), nullable=True)

def _achievement(r):
    _obj(r, 'achievement_id state_raw')
    _int(r['achievement_id'], positive=True)
    if r['state_raw'] is not None and (type(r['state_raw']) is not int or r['state_raw'] not in (0, 1)):
        raise ValueError('업적 상태 미확정')

def validate_document(doc, *, doll_ids=None, remolding_uids=None):
    if not isinstance(doc, dict) or set(doc) != {'schema_id', 'schema_version', 'sections', 'status'} or doc['schema_id'] != SCHEMA or (type(doc['schema_version']) is not int) or (doc['schema_version'] != VERSION):
        raise ValueError('지원하지 않는 계정 확장 형식')
    s, t = (doc['sections'], doc['status'])
    if not isinstance(s, dict) or not isinstance(t, dict) or set(t) != set(TYPES.values()) or (not set(s) <= set(t)):
        raise ValueError('계정 확장 섹션 오류')
    if any((x not in ('observed', 'missing', 'invalid', 'conflict', 'core_mismatch', 'reference_mismatch') for x in t.values())) or any(((k in s) != (x == 'observed') for k, x in t.items())):
        raise ValueError('계정 확장 상태 오류')
    if 'dolls' in s:
        d = _obj(s['dolls'], 'rows wardrobe_11101')
        _map(d['rows'], _doll, 'doll_id')
        _map(d['wardrobe_11101'], _state, 'costume_id')
        for r in d['rows'].values():
            if r['costume_state_raw'] is not None and r['costume_state_raw'] != d['wardrobe_11101'].get(str(r['costume_id']), {}).get('state_raw'):
                raise ValueError('의상 상태 참조 불일치')
    if 'remoldings' in s:
        _map(s['remoldings'], _remolding, 'uid')
    if 'wardrobe_11627' in s:
        _map(s['wardrobe_11627'], _state, 'costume_id')
    if 'support_unit_ids' in s:
        _arr(s['support_unit_ids'], lambda x: _int(x, positive=True))
    if 'support_chips' in s:
        d = _obj(s['support_chips'], 'normal special')
        _map(d['normal'], _chip, 'uid')
        _map(d['special'], lambda x: _chip(x, True), 'uid')
    if 'support_formations' in s:
        d = _obj(s['support_formations'], 'level selected_position_raw ammunition_ids formations')
        _int(d['level'], True)
        _int(d['selected_position_raw'], True)
        _arr(d['ammunition_ids'], _int)
        _arr(d['formations'], _form)
        if [x['position'] for x in d['formations']] != list(range(1, len(d['formations']) + 1)):
            raise ValueError('편성 순서 오류')
    if 'achievement_states' in s:
        d = _obj(s['achievement_states'], 'rows counted_state1')
        _map(d['rows'], _achievement, 'achievement_id')
        _int(d['counted_state1'])
        if d['counted_state1'] != sum((x['state_raw'] == 1 for x in d['rows'].values())):
            raise ValueError('업적 합계 불일치')
    probe = ExtensionCollector(dict(s), dict(t))
    normalized = probe.document(doll_ids=doll_ids, remolding_uids=remolding_uids)
    if normalized != doc:
        raise ValueError('계정 확장 참조 불일치')
    return doc

def read_document(raw, **kwargs):
    if len(raw) > MAX_BYTES:
        raise ValueError('계정 확장 크기 제한 초과')

    def no_duplicates(pairs):
        out = {}
        for k, v in pairs:
            if k in out:
                raise ValueError('중복 JSON 키')
            out[k] = v
        return out
    try:
        return validate_document(json.loads(raw, object_pairs_hook=no_duplicates), **kwargs)
    except (KeyError, TypeError, AttributeError, IndexError) as e:
        raise ValueError('계정 확장 구조 오류') from e
