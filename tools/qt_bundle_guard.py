"""Prevent OS DLLs / developer-tool DLLs from entering the Windows app."""
import os
from pathlib import Path, PureWindowsPath


def is_os_runtime(name):
    base = PureWindowsPath(name).name.lower()
    return (base in {'icuuc.dll', 'icuin.dll', 'ucrtbase.dll'}
            or (base.startswith('icudt') and base.endswith('.dll'))
            or base.startswith(('api-ms-win-', 'ext-ms-win-')))


def clean_build_path(environment, python_home):
    """Use only the selected Python and Windows dependency search directories."""
    windows = Path(environment.get('SystemRoot', r'C:\Windows'))
    python = Path(python_home)
    environment['PATH'] = os.pathsep.join(str(p) for p in
        (python, python/'DLLs', python/'Scripts', windows/'System32', windows)
        if p.is_dir())


def sanitize_binaries(entries):
    kept = []
    for entry in entries:
        name, source, kind = entry
        if is_os_runtime(name):
            print(f'[DLL guard] OS provides {name}; do not ship {source}')
            continue
        parts = set(Path(source).parts)
        if parts.intersection({'.codex', 'codex-runtimes', 'codex-primary-runtime'}):
            raise RuntimeError(f'Developer-tool DLL leaked into application: {source}')
        kept.append(entry)
    return kept
