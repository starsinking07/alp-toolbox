"""Build an isolated, validated release or prerelease setup with canonical names."""
from pathlib import Path
import argparse
import json
import os
import subprocess
import sys
import hashlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from brb02.diagnostics import APP_VERSION
from brb02.release_naming import setup_filename


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    channel = 'beta' if '-' in APP_VERSION else 'release'
    destination = (args.output_dir or ROOT/'dist'/f'{channel}-v{APP_VERSION}').resolve()
    destination.mkdir(parents=True, exist_ok=True)
    exe = destination/'Alp工具箱.exe'
    setup = destination/setup_filename(APP_VERSION)
    report = destination/'startup-check.json'
    if setup.exists():
        raise SystemExit(f'Refusing to overwrite an existing setup: {setup}')
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')
    def run(command, cwd=ROOT):
        subprocess.run([str(x) for x in command], cwd=cwd, env=env, check=True)
    run([sys.executable, '-B', '-m', 'PyInstaller', '--clean', '--noconfirm',
         '--distpath', destination, '--workpath', ROOT/'build'/f'{channel}-{APP_VERSION}',
         ROOT/'Alp.spec'])
    run([sys.executable, '-B', 'tools/check_packaged_qt.py', '--exe', exe,
         '--report', destination/'bundle-check.json'])
    # Diagnostic entry exits before elevation, single-instance pipes and hardware IO.
    subprocess.run([str(exe), '--startup-check', '--startup-report', str(report)],
                   cwd=ROOT, env=env, check=True, timeout=60,
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    result = json.loads(report.read_text(encoding='utf-8'))
    if not result.get('ok') or result.get('device_modules_loaded'):
        raise SystemExit('Frozen Qt startup validation failed')
    run([ROOT/'installer/nsis/nsis-3.10/makensis.exe', f'/DAPP_VERSION={APP_VERSION}',
         f'/DAPP_BINARY_PATH={exe}', f'/DSETUP_OUT={setup}', 'Alp-setup.nsi'], ROOT/'installer')
    sums = []
    for path in (exe, setup):
        with path.open('rb') as stream:
            sums.append(f'{hashlib.file_digest(stream, "sha256").hexdigest()}  {path.name}')
    (destination/'SHA256SUMS.txt').write_text('\n'.join(sums)+'\n', encoding='utf-8')
    print(f'Build complete: {setup}', flush=True)


if __name__ == '__main__':
    main()
