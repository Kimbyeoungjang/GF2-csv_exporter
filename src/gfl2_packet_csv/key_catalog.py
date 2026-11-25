"""TalentKeyData reference categories and current positional joins."""
import csv
from collections import Counter
from .reference_data import lru_cache
from .reference_data import files

@lru_cache(maxsize=1)
def catalog():
    with files('gfl2_packet_csv.static_tables').joinpath('talent_key_types.csv').open(encoding='utf-8',newline='') as f:
        return {int(r['key_id']):(int(r['key_type']) if r['key_type'] else None,int(r['doll_id']) if r['doll_id'] else None) for r in csv.DictReader(f)}

def project(key_id):
    entry=catalog().get(int(key_id))
    if entry is None: return None,'unrecognized'
    if entry[0] not in (2,4): return None,'unexpected_inventory_type'
    return entry[0],'catalog_consistent'

def audit(document, common_keys, *, inventory_available):
    result={'schema_version':1,'inventory_available':inventory_available,'status':'doll_snapshot_unavailable','by_kind':{}}
    if not document or document['status'].get('dolls')!='observed': return result
    cat=catalog(); result['status']='observed'
    for kind,typ,uid_field in [('fixed',1,None),('common',2,'common_key_uids_raw'),('expansion',3,None),('bond',4,'bond_key_uids_raw')]:
        c=Counter()
        for d in document['sections']['dolls']['rows'].values():
            ids=d[kind+'_key_ids_raw']; uids=d[uid_field] if uid_field else None
            if ids is None:
                c['id_sequence_absent']+=1; continue
            if uid_field:
                c['paired_sequence_equal_length' if uids is not None and len(uids)==len(ids) else 'paired_sequence_unavailable_or_length_mismatch']+=1
            for pos,key_id in enumerate(ids):
                if not key_id: c['empty']+=1
                else:
                    entry=cat.get(key_id); status='catalog_consistent' if entry and entry[0]==typ and (uid_field or entry[1]==d['doll_id']) else 'catalog_mismatch'
                    c[status]+=1
                if uid_field and uids is not None and pos<len(uids):
                    uid=int(uids[pos] or 0)
                    if not key_id and not uid: c['paired_empty']+=1
                    elif not inventory_available: c['inventory_unavailable']+=1
                    elif uid and common_keys.get(uid,{}).get('key_id')==key_id: c['paired_model_verified']+=1
                    else: c['paired_model_mismatch']+=1
        result['by_kind'][kind]=dict(sorted(c.items()))
    return result
