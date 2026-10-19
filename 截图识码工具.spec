# -*- mode: python ; coding: utf-8 -*-
import os
import subprocess
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

spec_dir = Path(globals().get("SPECPATH", os.getcwd()))
bundle_dir = spec_dir / 'build_assets' / 'tesseract_bin'
if (spec_dir / 'tesseract_bin' / 'tesseract.exe').exists():
    if not bundle_dir.exists():
        subprocess.check_call([
            sys.executable,
            str(spec_dir / 'tools' / 'prepare_tesseract_bundle.py'),
        ])
else:
    bundle_dir.mkdir(parents=True, exist_ok=True)

system_root = Path(os.environ.get('SystemRoot', r'C:\Windows'))
safe_path = [
    system_root / 'System32',
    system_root,
    system_root / 'System32' / 'Wbem',
    system_root / 'System32' / 'WindowsPowerShell' / 'v1.0',
    Path(sys.executable).parent,
]
os.environ['PATH'] = os.pathsep.join(
    str(path) for path in dict.fromkeys(safe_path)
)

datas = [(str(spec_dir / 'icon.ico'), '.'), (str(bundle_dir), 'tesseract_bin')]
binaries = []
hiddenimports = ['keyboard']
tmp_ret = collect_all('pyzbar')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    [str(spec_dir / 'main.py')],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='截图识码工具',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(spec_dir / 'icon.ico')],
)
