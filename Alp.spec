# -*- mode: python ; coding: utf-8 -*-
"""Alp 工具箱打包配置 (PyInstaller 6.x, onefile)。

构建: pyinstaller --clean -y Alp.spec
产物: dist/Alp工具箱.exe
"""
import os
from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(SPECPATH)
LHM_DLL = os.path.join(ROOT, 'LHM', 'LibreHardwareMonitorLib.dll')

datas = [
    (os.path.join(ROOT, 'brb02', 'gui', 'fonts'), 'brb02/gui/fonts'),
]
binaries = []
if os.path.isfile(LHM_DLL):
    binaries.append((LHM_DLL, 'LHM'))

hiddenimports = []
for pkg in ('libusb_package', 'bleak'):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h


a = Analysis(
    ['main.py'],
    pathex=[ROOT],
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
    name='Alp工具箱',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=os.path.join(ROOT, 'tools', 'app.ico'),
)
