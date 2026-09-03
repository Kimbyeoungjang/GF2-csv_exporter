# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all

# PyInstaller executes a .spec with SPECPATH set to the directory containing
# the spec file.  Resolve every project path from there so a cwd change cannot
# turn "packaging/helper_entry.py" into "packaging/packaging/helper_entry.py".
SPEC_DIR = os.path.abspath(SPECPATH)
PROJECT_ROOT = os.path.abspath(os.path.join(SPEC_DIR, os.pardir))
ENTRY_SCRIPT = os.path.join(SPEC_DIR, "helper_entry.py")
SRC_DIR = os.path.join(PROJECT_ROOT, "src")

pydivert_datas, pydivert_binaries, pydivert_hidden = collect_all("pydivert")

a = Analysis(
    [ENTRY_SCRIPT],
    pathex=[SRC_DIR],
    binaries=pydivert_binaries,
    datas=pydivert_datas,
    hiddenimports=pydivert_hidden + ["psutil"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide6", "mitmproxy"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="windows-packet-capture",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=True,
    argv_emulation=False,
    uac_admin=True,
)
