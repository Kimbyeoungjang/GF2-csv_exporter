# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all

SPEC_DIR = os.path.abspath(SPECPATH)
PROJECT_ROOT = os.path.abspath(os.path.join(SPEC_DIR, os.pardir))
ENTRY_SCRIPT = os.path.join(SPEC_DIR, "packet_entry.py")
SRC_DIR = os.path.join(PROJECT_ROOT, "src")

pydivert_datas, pydivert_binaries, pydivert_hidden = collect_all("pydivert")
# importlib.resources catalogs must be explicitly bundled in frozen builds.
static_datas = [
    (os.path.join(SRC_DIR, "gfl2_packet_csv", package, "*.csv"),
     os.path.join("gfl2_packet_csv", package))
    for package in ("static_tables", "static_achievements")
]

a = Analysis(
    [ENTRY_SCRIPT],
    pathex=[SRC_DIR],
    binaries=pydivert_binaries,
    datas=pydivert_datas + static_datas + [(os.path.join(SRC_DIR, "gfl2_packet_csv", "reference_seed", "*"), "gfl2_packet_csv/reference_seed")],
    hiddenimports=pydivert_hidden + ["gfl2_packet_csv.static_tables", "gfl2_packet_csv.static_achievements"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["mitmproxy"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GFL2-Packet-CSV-Exporter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=True,
    argv_emulation=False,
    uac_admin=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="GFL2-Packet-CSV-Exporter",
)
