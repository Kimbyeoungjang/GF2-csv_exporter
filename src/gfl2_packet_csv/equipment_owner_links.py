"""Verified current item -> doll ownership; ordinary account CSV only.

11138.3 and 11163.4 were verified by controlled equip/unequip captures.
Absence, explicit zero and conflict remain distinct. Do not publish any
unverified field or account identifiers in the count-only metadata.
"""
from collections import Counter, defaultdict

FILE = 'equipment_owner_links.csv'
HEADERS = ['kind','uid','owner_field_present','owner_doll_id','reference_status']


def build_owner_links(unmapped, *, common_keys, remoldings, dolls, doll_extension, available):
    owners = {'common_key':defaultdict(set),'remolding':defaultdict(set)}
    doll_ready = 11101 in available and doll_extension is not None
    if doll_ready:
        for row in doll_extension['rows'].values():
            did = int(row['doll_id'])
            for field in ('common_key_uids_raw','bond_key_uids_raw'):
                for uid in row[field] or ():
                    if uid:
                        owners['common_key'][int(uid)].add(did)
            for uid in (row.get('core') or {}).get('remolding_slots') or ():
                if uid:
                    owners['remolding'][int(uid)].add(did)
    rows=[]
    counts=Counter()
    observed=[]
    for packet,field,kind,inventory in ((11138,3,'common_key',common_keys),(11163,4,'remolding',remoldings)):
        if packet not in available:
            continue
        observed.append(kind)
        for uid in sorted(map(int,inventory)):
            raw=unmapped.get((packet,uid,field),[])
            # Decoder already checks owner shape; fail closed if injected.
            if raw and (len(raw)!=1 or raw[0][0]!=0):
                present=1;owner=None;status='invalid_owner_field'
            else:
                present=int(bool(raw))
                owner=int(raw[0][1]) if raw else None
                current=owners[kind].get(uid,set())
                if not doll_ready:
                    status='doll_snapshot_unavailable'
                    owner=None if owner else owner
                elif not owner:
                    status='slot_only' if current else 'detached'
                elif owner not in dolls:
                    status='unmatched_doll';owner=None
                elif current=={owner}:
                    status='verified'
                elif current:
                    status='slot_mismatch'
                else:
                    status='owner_only'
            counts[status]+=1
            rows.append(dict(zip(HEADERS,(kind,uid,present,owner,status))))
    return rows,{'schema_version':1,'source_fields':['11138.3','11163.4'],
                 'observed_kinds':observed,'doll_snapshot_available':doll_ready,
                 'status_counts':dict(sorted(counts.items())), 'rows':len(rows),
                 'file':FILE if observed else None}
