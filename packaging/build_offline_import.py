"""Generate a separate, standard-library-only GF2Tools importer source tree.
No capture, GUI, updater or helper entry points are distributed here.
Run: python packaging/build_offline_import.py OUTPUT_DIRECTORY
Then: python -m pip wheel --no-deps --no-build-isolation OUTPUT_DIRECTORY
"""
import argparse
import ast
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'src' / 'gfl2_packet_csv'
PACKAGE = 'gf2_account_import'
ENTRY = {'account_import_session', 'backup_preflight'}
ALLOWED = set('reference_data ''account_csv account_export_scope account_extension account_import_session account_panel account_semantics achievement_catalog additional_doll additional_doll_details atomic_output attachment_affixes backup_preflight bundle_import calibration_completion common_key_enhancement_catalog costume_integrity dataset_catalog doll_growth_catalog doll_promotion doll_stat_catalog dorm_costumes embedded_states equipment_graph_audit equipment_owner_links equipment_state formation_slots growth_skill_state helix_catalog helix_effect_catalog item_catalog key_catalog key_evolution panel_equipment panel_ledger panel_references panel_state panel_state_evidence progress_catalog progress_group_references progress_snapshot progress_task_candidate progression_reference_catalog remolding_catalog skill_parameters skill_strategy_order support_attribute_totals support_capacity support_catalog support_integrity support_stage_candidates task_reference_catalog weapon_growth_catalog weapon_property_catalog weapon_skin_catalog weapon_slot_observations wire_probe'''.split())
# Only dependencies needed by the entry points; fail closed on new imports.
def closure(source=SOURCE):
    pending = set(ENTRY)
    seen = set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        if name not in ALLOWED:
            raise ValueError(f'Unreviewed local module: {name}')
        tree = ast.parse((source / f'{name}.py').read_text(encoding='utf-8'))
        seen.add(name)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level:
                if node.level != 1:
                    raise ValueError('Unexpected relative import')
                pending.update([node.module.split('.')[0]] if node.module else [a.name for a in node.names])
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [node.module] if isinstance(node, ast.ImportFrom) else [a.name for a in node.names]
                if any(n.split('.')[0] not in sys.stdlib_module_names for n in names):
                    raise ValueError(f'Non-stdlib import in {name}')
    if seen != ALLOWED:
        raise ValueError('Offline allowlist changed; review required')
    return seen


def generate(output, source=SOURCE):
    modules = closure(source)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    dest = output / 'src' / PACKAGE
    dest.mkdir(parents=True)
    for name in sorted(modules):
        text = (source / f'{name}.py').read_text(encoding='utf-8')
        # The existing static resource references are literal package names.
        for resource in ('static_tables', 'static_achievements'):
            text = text.replace(f'gfl2_packet_csv.{resource}', f'{PACKAGE}.{resource}')
        (dest / f'{name}.py').write_text(text, encoding='utf-8')
    for resource in ('static_tables', 'static_achievements'):
        target = dest / resource
        target.mkdir()
        for p in sorted((source / resource).iterdir()):
            if p.name == '__init__.py' or p.suffix == '.csv':
                shutil.copyfile(p, target / p.name)
    (dest / '__init__.py').write_text('''"""Local CSV-only account import entry points. No capture or GUI integration."""\nfrom .account_import_session import read_account_session\nfrom .backup_preflight import check_backup, exit_code\n__version__ = "1.6.0"\n__all__ = ["read_account_session", "check_backup", "exit_code"]\n''', encoding='utf-8')
    (dest / '__main__.py').write_text('''import argparse\nimport json\nfrom .backup_preflight import check_backup, exit_code\n\ndef main():\n    parser = argparse.ArgumentParser(description="Check local GF2 account backup")\n    parser.add_argument("bundle")\n    parser.add_argument("--reference-index")\n    args = parser.parse_args()\n    report = check_backup(args.bundle, reference_index=args.reference_index)\n    print(json.dumps(report, ensure_ascii=False, sort_keys=True))\n    return exit_code(report)\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n''', encoding='utf-8')
    shutil.copyfile(ROOT / 'LICENSE', output / 'LICENSE')
    (output / 'pyproject.toml').write_text('''[build-system]\nrequires = ["setuptools>=75", "wheel"]\nbuild-backend = "setuptools.build_meta"\n\n[project]\nname = "gf2-account-import"\nversion = "1.6.0"\ndescription = "Offline GF2Tools account CSV import and verification"\nrequires-python = ">=3.11,<3.14"\ndependencies = []\nlicense = {file = "LICENSE"}\n\n[tool.setuptools.packages.find]\nwhere = ["src"]\n\n[tool.setuptools.package-data]\n"gf2_account_import.static_achievements" = ["*.csv"]\n"gf2_account_import.static_tables" = ["*.csv"]\n''', encoding='utf-8')
    (output / 'README.md').write_text('''# GF2Tools offline account importer\n\nGenerated from Exporter 1.6.0 source. Python 3.11–3.13; no external runtime dependencies.\n\n```python\nfrom gf2_account_import import read_account_session\naccount = read_account_session("gfl2_user_csv_bundle.zip")\n```\n\nCheck local files: `python -m gf2_account_import gfl2_user_csv_bundle.zip`. Exit codes: 0 base and both states valid (integrated or legacy), 4 optional sidecar missing/invalid, 8 base invalid. No capture, GUI, updater or helper programs are included. Shared pure-data modules still contain unused byte parsing and CSV writing functions; the exposed import/check paths do not call those. Results contain private account data: do not log or upload them. Existing uncertainty labels remain unchanged. License: see LICENSE.\n\nReference snapshots: omit `reference_index` for legacy V35/baseline backups; for other snapshots, pass a trusted local `reference_data.json` index via `read_account_session(..., reference_index=...)`, `check_backup(..., reference_index=...)`, or `python -m gf2_account_import BUNDLE --reference-index INDEX`. No network access. Reference mismatches return exit code 8.\n''', encoding='utf-8')
    return len(modules)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    print(f'Generated {generate(parser.parse_args().output)} shared modules')
