"""Optional login panel observations. Only allowlisted semantic scalars leave memory.
The V35 backup is unchanged; a separately bound CSV is not a raw-packet dump.
"""
from dataclasses import dataclass, field
from pathlib import Path
import csv
import hashlib
from .wire_probe import parse_fields, WireDecodeError
from .atomic_output import atomic_output

LEGACY_SCHEMA = 'gf2-panel-state-1'
LEGACY_FIELDS = ('schema','bundle_sha256','doll_id','management_stage_candidate',
          'oath_state_candidate','food_observation','attack_xp_candidate',
          'acid_xp_candidate','unmapped_food_nonzero')
V2_SCHEMA = 'gf2-panel-state-2'
V2_FIELDS = (*LEGACY_FIELDS[:7], 'defense_xp_candidate', 'hp_xp_candidate',
             'element_food_nonzero', 'unmapped_food_nonzero')
SCHEMA = 'gf2-panel-state-3'
FIELDS = (*V2_FIELDS[:9], 'element_xp_candidate', 'unmapped_food_nonzero')


def scalar(fields, n, wire=0):
    values = [(w,v) for k,w,v in fields if k == n]
    if not values:
        return None
    if len(values)!=1 or values[0][0]!=wire:
        raise ValueError('panel observation wire mismatch')
    return values[0][1]


def food_observation(raw):
    if raw is None:
        return ('absent',None,None,None,None,None)
    if type(raw) is not int or not 0 <= raw < 2**64:
        raise ValueError('invalid packed food')
    b=raw.to_bytes(8,'little')
    return ('observed',b[0],b[1],b[2],b[7],int(any(b[3:7])))


@dataclass
class PanelObservations:
    management_stage: int | None = None
    management_conflict: bool = False
    dolls: dict = field(default_factory=dict)
    conflicts: set = field(default_factory=set)
    doll_invalid: bool = False

    def observe(self, packet, raw):
        if packet not in (11101,22001): return
        try:
            fields=parse_fields(raw,max_fields=30000)
            if packet==22001:
                nested=scalar(fields,1,2)
                level=scalar(parse_fields(nested,max_fields=1000),1) if nested is not None else None
                if type(level) is not int or not 1<=level<=36: raise ValueError('invalid management stage')
                if self.management_stage is not None and self.management_stage!=level:
                    self.management_conflict=True
                self.management_stage=level
                return
            current={}
            for n,w,v in fields:
                if n!=1:continue
                if w!=2:raise ValueError('invalid doll envelope')
                record=parse_fields(v,max_fields=5000)
                did=scalar(record,1)
                if not did or did in current:raise ValueError('invalid doll identifier')
                affinity=scalar(record,50,2)
                oath='unknown'
                if affinity is not None:
                    a=parse_fields(affinity,max_fields=1000)
                    flag=scalar(a,4)
                    paired=scalar(a,5) is not None
                    if flag==1 and paired:oath='positive_flag'
                    elif flag is None and not paired:oath='both_absent'
                current[did]=(oath,*food_observation(scalar(record,61)))
            if not current and (fields or self.dolls):raise ValueError('ambiguous doll snapshot')
            for did,value in current.items():
                if did in self.dolls and self.dolls[did]!=value:self.conflicts.add(did)
                self.dolls[did]=value
        except (WireDecodeError,ValueError,TypeError,OverflowError,IndexError):
            if packet==22001:self.management_conflict=True
            else:self.doll_invalid=True

    def publish(self, bundle, doll_ids, *, binding_sha=None, destination=None):
        """Publish only alongside a completed backup; never persist unknown bytes/timestamps."""
        bundle=Path(bundle)
        destination = Path(destination) if destination is not None else bundle.with_suffix('.panel-state.csv')
        sha = binding_sha
        if sha is None:
            with bundle.open('rb') as f: sha = hashlib.file_digest(f,'sha256').hexdigest()
        mg=None if self.management_conflict else self.management_stage
        with atomic_output(destination) as tmp:
            with tmp.open('w',encoding='utf-8-sig',newline='') as f:
                writer=csv.writer(f); writer.writerow(FIELDS)
                for did in sorted(doll_ids):
                    row=self.dolls.get(did)
                    if self.doll_invalid or did in self.conflicts or row is None:
                        row=('unknown','unavailable',None,None,None,None,None)
                    writer.writerow((SCHEMA,sha,did,mg,*row))
        return destination
