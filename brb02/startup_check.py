"""Frozen runtime check without device connections, elevation or visible UI."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import platform
import sys
import traceback


def loaded_module_path(name):
    if os.name != 'nt':
        return None
    kernel = ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    kernel.GetModuleHandleW.restype = ctypes.c_void_p
    kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p,ctypes.c_wchar_p,ctypes.c_uint]
    handle = kernel.GetModuleHandleW(name)
    if not handle:
        return None
    buffer = ctypes.create_unicode_buffer(32768)
    return buffer.value if kernel.GetModuleFileNameW(handle,buffer,len(buffer)) else None


def run_cli():
    parser = argparse.ArgumentParser()
    parser.add_argument('--startup-check',action='store_true')
    parser.add_argument('--startup-report',type=Path,default=Path(
        os.environ.get('APPDATA','.'))/'Brb02Toolbox'/'startup-check.json')
    args = parser.parse_args()
    args.startup_report.parent.mkdir(parents=True,exist_ok=True)
    report = {'ok':False,'phase':'importing_qt','python':sys.version,
              'system':platform.platform(),'machine':platform.machine(),
              'frozen':bool(getattr(sys,'frozen',False))}
    args.startup_report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    try:
        import PySide6
        from PySide6 import QtCore, QtGui, QtNetwork, QtSvg, QtWidgets
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        canvas = QtGui.QImage(32,32,QtGui.QImage.Format_ARGB32)
        painter = QtGui.QPainter(canvas)
        painter.fillRect(canvas.rect(),QtGui.QColor('#2f6df6'))
        painter.end()
        report.update(ok=canvas.pixelColor(0,0).name()=='#2f6df6',phase='complete',
                      qt=QtCore.qVersion(),pyside=PySide6.__version__,
                      platform_plugin=app.platformName(),
                      icu_loaded_from=loaded_module_path('icuuc.dll'))
    except Exception:
        report.update(phase='failed',error=traceback.format_exc())
    report['device_modules_loaded'] = [name for name in
        ('brb02.service','brb02.device','brb02.transport_usb','brb02.transport_ble') if name in sys.modules]
    if report['device_modules_loaded']:
        report['ok'] = False
    args.startup_report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if report['ok'] else 1
