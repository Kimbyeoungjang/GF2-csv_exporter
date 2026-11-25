"""Single-snapshot offline import for GF2Tools. No game or capture access.

Only complete V35 bundles are accepted. Optional files are independently
validated; missing or rejected data never silently become zero. Returned
results are private account data, not suitable for diagnostic logging.
"""
from pathlib import Path
import csv
from .bundle_import import read_bundle, bound_archive_digest
from .panel_references import read_static_csv, read_panel_state
from .equipment_state import read_equipment_state
from .account_panel import _panel_from_bundle
from .account_semantics import _semantics_from_bundle
from .growth_skill_state import growth_skill_states
from .support_stage_candidates import support_stage_candidates
from .progress_group_references import progress_group_references

SCHEMA = 'gf2-account-import-session-1'


def _optional(reader, path):
    try:
        return 'valid', reader(path)
    except FileNotFoundError:
        return ('invalid' if isinstance(path, bytes) or path.exists() else 'missing'), None
    except (OSError, ValueError, csv.Error):
        return 'invalid', None


def read_account_session(path, *, panel_state_csv=None, equipment_state_csv=None,
                         require_sidecars=False, reference_index=None):
    """Import with the historic baseline or a trusted matching local reference index."""
    from .reference_data import baseline, load_index, use_snapshot
    snapshot = baseline() if reference_index is None else load_index(reference_index)
    with use_snapshot(snapshot):
        return _read_account_session(path, panel_state_csv=panel_state_csv, equipment_state_csv=equipment_state_csv, require_sidecars=require_sidecars)

def _read_account_session(path, *, panel_state_csv=None, equipment_state_csv=None,
                          require_sidecars=False):
    """Parse the base once; derive all views from that same validated bundle.

    Default sidecars use the matching filename. Explicit paths are supported.
    A bad optional file is excluded and its status returned, unless both are
    required. Calculation hypotheses remain disabled. A base ZIP replacement
    detected before returning aborts the whole import. This is not an atomic
    filesystem snapshot or an authentication guarantee.
    """
    path = Path(path)
    bundle = read_bundle(path)
    if not bundle.complete_capture:
        raise ValueError('complete bundle required')
    static = read_static_csv()
    control, food, *_ = static
    panel_path = Path(panel_state_csv) if panel_state_csv is not None else path.with_suffix('.panel-state.csv')
    equipment_path = Path(equipment_state_csv) if equipment_state_csv is not None else path.with_suffix('.equipment-state.csv')
    if bundle.embedded_states:
        if panel_state_csv is not None or equipment_state_csv is not None:
            raise ValueError('integrated backup already contains state CSVs')
        panel_path = bundle.embedded_states['panel_state']
        equipment_path = bundle.embedded_states['equipment_state']
    ps, state = _optional(lambda p: read_panel_state(p, path, bundle, control, food), panel_path)
    es, equipment = _optional(lambda p: read_equipment_state(p, path, bundle), equipment_path)
    if require_sidecars and (ps != 'valid' or es != 'valid'):
        raise ValueError('required state CSV missing or invalid')
    from .reference_data import current
    result = {
        'schema': SCHEMA,
        'reference_data': current().metadata(),
        'bundle_sha256': bundle.archive_sha256,
        'sidecar_status': {'panel_state': ps, 'equipment_state': es},
        'rows': bundle.rows,
        'panel': _panel_from_bundle(bundle, state if state is not None else (None, {}), static),
        'semantics': _semantics_from_bundle(bundle, equipment),
        'growth_skills': growth_skill_states(bundle),
        'support_stages': support_stage_candidates(bundle),
        'progress_references': progress_group_references(bundle),
    }
    bound_archive_digest(path, bundle)
    return result
