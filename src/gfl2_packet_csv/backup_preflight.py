"""Read-only verification of the three local backup files; aggregate output only."""
from pathlib import Path
import csv
from .bundle_import import read_bundle, bound_archive_digest
from .panel_references import read_panel_state, read_static_csv
from .equipment_state import read_equipment_state


SIDE_CARS = (('panel_state', '.panel-state.csv'), ('equipment_state', '.equipment-state.csv'))


def check_backup(path, *, reference_index=None):
    from .reference_data import baseline, load_index, use_snapshot
    try:
        snapshot = baseline() if reference_index is None else load_index(reference_index)
    except (OSError, ValueError):
        return {'schema': 'gf2-backup-preflight-1', 'bundle': 'reference_unavailable',
                'panel_state': 'not_checked', 'equipment_state': 'not_checked'}
    with use_snapshot(snapshot):
        return _check_backup(path)

def _check_backup(path):
    """Return file integrity status, not a semantic completeness or authenticity claim.

    Sidecars are optional for historic V35 backups. An invalid optional file does
    not invalidate the base ZIP. No record IDs or file paths are returned.
    """
    path = Path(path)
    result = {'schema': 'gf2-backup-preflight-1', 'bundle': 'invalid',
              'panel_state': 'not_checked', 'equipment_state': 'not_checked'}
    from .reference_data import ReferenceMismatch
    try:
        bundle = read_bundle(path)
    except ReferenceMismatch:
        result['bundle'] = 'reference_mismatch'
        return result
    except (OSError, ValueError):
        return result
    result['bundle'] = 'valid_complete_capture'
    for key, suffix in SIDE_CARS:
        side = bundle.embedded_states.get(key, path.with_suffix(suffix))
        try:
            if key == 'panel_state':
                control, food, *_ = read_static_csv()
                read_panel_state(side, path, bundle, control, food)
            else:
                read_equipment_state(side, path, bundle)
        except FileNotFoundError:
            result[key] = 'missing' if not isinstance(side, bytes) and not side.exists() else 'invalid'
        except (OSError, ValueError, csv.Error):
            result[key] = 'invalid'
        else:
            result[key] = 'valid'
    # A concurrent replacement of the base invalidates this combined report.
    try:
        bound_archive_digest(path, bundle)
    except (OSError, ValueError):
        result.update(bundle='changed_during_check', panel_state='not_checked', equipment_state='not_checked')
    return result


def exit_code(report):
    if report['bundle'] != 'valid_complete_capture':
        return 8
    return 0 if all(report[k] == 'valid' for k, _ in SIDE_CARS) else 4
