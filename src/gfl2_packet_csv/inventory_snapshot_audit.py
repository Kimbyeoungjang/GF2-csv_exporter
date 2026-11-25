"""Offline comparison of complete remolding and item inventories.

Only aggregate counts are returned. Static catalogs are shared, but the
projection logic is separate from the exporter. Not a partial-update audit.
"""
from collections import Counter
from .doll_snapshot_audit import _fields, _scalar, _vector
from .equipment_snapshot_audit import _Audit, _messages, _keyed
from .remolding_catalog import models, options
from .item_catalog import catalog


def audit_remolding_snapshot(payload, rows):
    records = {}
    for r in _messages(_fields(payload), 1):
        uid = _scalar(r, 3)
        if not uid or uid in records:
            raise ValueError('Missing or duplicate remolding UID')
        records[uid] = r
    if not records:
        raise ValueError('Empty remolding inventory outside audit scope')
    owners = [r for r in rows['equipment_owner_links.csv'] if r['kind'] == 'remolding']
    audit = _Audit({**rows, 'equipment_owner_links.csv': owners}, [
        ('remolding_details.csv', ('uid',)), ('remolding_options.csv', ('uid','position')),
        ('equipment_owner_links.csv', ('uid',))])
    doll_ids = {int(r['doll_id']) for r in rows['dolls.csv']}
    model_refs, option_refs = models(), options()
    counts = Counter()
    for uid, r in records.items():
        model, owner = _scalar(r, 1), _scalar(r, 4)
        opts = _vector(r, 2)
        if not model:
            raise ValueError('Missing remolding model')
        status = 'catalog_unavailable' if not model_refs else 'catalog_consistent' if model in model_refs else 'unrecognized'
        audit.row('remolding_details.csv', (uid,), dict(model_id=model, model_reference_status=status))
        audit.row('equipment_owner_links.csv', (uid,), dict(owner_field_present=int(owner is not None),
            owner_doll_id=owner if not owner or owner in doll_ids else None))
        counts['owner_present'] += owner is not None
        counts['option_field_absent'] += opts is None
        opts = opts or ()
        counts['option_rows'] += len(opts)
        counts['duplicate_option_occurrences'] += len(opts)-len(set(opts))
        counts['model_unrecognized'] += status != 'catalog_consistent'
        for i, opt in enumerate(opts, 1):
            if not opt:
                raise ValueError('Invalid remolding option ID')
            ref = option_refs.get(opt)
            group, level = ref if ref is not None else (None, None)
            status = 'catalog_consistent' if ref is not None else 'unrecognized' if option_refs else 'catalog_unavailable'
            audit.row('remolding_options.csv', (uid,i), dict(option_id=opt, feature_group_id=group,
                option_level=level, option_reference_status=status))
            counts['option_unrecognized'] += ref is None
    return {'remoldings':len(records), 'observations':dict(counts), **audit.result()}


def audit_item_snapshot(payload, rows):
    records = _keyed(_fields(payload), 2)
    if not records:
        raise ValueError('Empty item inventory outside audit scope')
    audit = _Audit(rows, [('items.csv', ('item_id',))])
    refs = catalog()
    counts = Counter()
    for item, r in records.items():
        quantity = _scalar(r, 2)
        if quantity is None:
            raise ValueError('Missing item quantity')
        ref = refs.get(item)
        if ref is None:
            category = rarity = affinity = None
            status = 'unrecognized_item' if refs else 'catalog_unavailable'
            gift_status = 'item_reference_unavailable'
        else:
            category, rarity, listed, affinity = ref
            status = 'catalog_consistent'
            gift_status = ('catalog_candidate' if affinity is not None else 'gift_value_absent') if listed else 'not_in_gift_catalog'
        audit.row('items.csv', (item,), dict(quantity=quantity, item_category_code=category,
            item_rarity_code=rarity, item_reference_status=status,
            gift_base_affinity_candidate=affinity, gift_reference_status=gift_status))
        counts['explicit_zero_quantity'] += quantity == 0
        counts['unrecognized_item'] += ref is None
        counts['gift_candidates'] += gift_status == 'catalog_candidate'
    return {'items':len(records), 'observations':dict(counts), **audit.result()}
