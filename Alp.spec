# -*- mode: python ; coding: utf-8 -*-
"""Alp 工具箱打包配置 (PyInstaller 6.x, onefile)。

构建: pyinstaller --clean -y Alp.spec
产物: dist/Alp工具箱.exe
"""
import os
from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(SPECPATH)
LHM_DIR = os.path.join(ROOT, 'LHM')
LHM_DLL = os.path.join(LHM_DIR, 'LibreHardwareMonitorLib.dll')

datas = [
    (os.path.join(ROOT, 'brb02', 'gui', 'fonts'), 'brb02/gui/fonts'),
    (os.path.join(ROOT, 'assets', 'app.ico'), 'assets'),      # 关于页图标
]
# PawnIO 安装器随包分发: LHM 0.9.6 起 CPU 温度依赖它, 应用内"一键安装"要用
PAWNIO_SETUP = os.path.join(ROOT, 'installer', 'pawnio', 'PawnIO_setup.exe')
if os.path.isfile(PAWNIO_SETUP):
    datas.append((PAWNIO_SETUP, 'assets'))
binaries = []
if os.path.isfile(LHM_DLL):
    # 0.9.6 起 LHM 改用 PawnIO 驱动, 并新增 System.Memory / DiskInfoToolkit /
    # BlackSharp.Core 等托管依赖 —— 少任何一个都会在 Computer.Open() 抛
    # FileNotFoundException, 表现为 CPU 温度恒为 0, 所以整个目录一起打包。
    lhm_files = sorted(f for f in os.listdir(LHM_DIR) if f.lower().endswith('.dll'))
    for fn in lhm_files:
        binaries.append((os.path.join(LHM_DIR, fn), 'LHM'))
    if len(lhm_files) < 2:
        raise SystemExit('LHM 依赖不全: LHM/ 下应同时存在 LibreHardwareMonitorLib.dll '
                         '与 System.Memory / DiskInfoToolkit 等依赖')
else:
    # 静默跳过会导致成品 CPU 温度永远显示 "--", 必须让构建直接失败
    raise SystemExit('LHM dll 缺失: LHM/LibreHardwareMonitorLib.dll')

hiddenimports = ['pyaudiowpatch', 'pynput']   # 音频同步/响应灯效 (动态 import 于 audio_sync/keypress_sync)
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
    icon=os.path.join(ROOT, 'assets', 'app.ico'),
)
