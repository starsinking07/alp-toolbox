"""Inspect a frozen executable without running it or connecting to hardware."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from qt_bundle_guard import is_os_runtime


def inspect(executable):
    from PyInstaller.archive.readers import CArchiveReader
    archive = CArchiveReader(str(executable))
    forbidden = [name for name in archive.toc if is_os_runtime(name)]
    required = ['PySide6\\Qt6Core.dll', 'PySide6\\QtCore.pyd']
    missing = [name for name in required if name not in archive.toc]
    return {'ok':not forbidden and not missing, 'forbidden_os_dlls':forbidden,
            'missing_qt_modules':missing,
            'sha256':hashlib.sha256(executable.read_bytes()).hexdigest()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', required=True, type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    result = inspect(args.exe)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['ok'] else 1)
