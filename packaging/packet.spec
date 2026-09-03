# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all

SPEC_DIR = os.path.abspath(SPECPATH)
PROJECT_ROOT = os.path.abspath(os.path.join(SPEC_DIR, os.pardir))
ENTRY_SCRIPT = os.path.join(SPEC_DIR, "packet_entry.py")
SRC_DIR = os.path.join(PROJECT_ROOT, "src")

pydivert_datas, pydivert_binaries, pydivert_hidden = collect_all("pydivert")

a = Analysis(
    [ENTRY_SCRIPT],
    pathex=[SRC_DIR],
    binaries=pydivert_binaries,
    datas=pydivert_datas,
    hiddenimports=pydivert_hidden,
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
    disable_windowed_traceback=False,
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
