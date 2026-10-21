# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path
from PyInstaller.utils.hooks import copy_metadata

root = Path(SPECPATH)
# Direct spec builds share the same version resolver as scripts/build.ps1.
import sys
sys.path.insert(0, str(root))
from scripts.release import build_metadata, current_version
build_metadata(os.environ.get('LOGOPEN_BUILD_VERSION') or current_version(root), root)
runtime_data = [
    (str(root / 'ui' / 'style.qss'), 'ui'),
    (str(root / 'build' / 'version' / 'VERSION'), '.'),
    (str(root / 'requirements.txt'), '.'),
    (str(root / 'docs' / 'distribution.md'), 'docs'),
    (str(root / 'docs' / 'event_sources.md'), 'docs'),
]
for line in (root / 'requirements.txt').read_text(encoding='utf-8').splitlines():
    if '==' in line and not line.lstrip().startswith('#'):
        runtime_data += copy_metadata(line.split('==', 1)[0].strip())

a = Analysis(
    [str(root / 'desktop.py')],
    pathex=[],
    binaries=[],
    datas=runtime_data,
    hiddenimports=['win32cred', 'win32evtlog', 'win32evtlogutil'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pytest', 'unittest', 'tkinter'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='LogOpen',
    icon=str(root / 'assets' / 'logopen.ico'),
    version=str(root / 'build' / 'version' / 'windows-version.txt'),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='LogOpen',
)
