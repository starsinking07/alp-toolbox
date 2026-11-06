"""Public artifact names shared by release builds and the updater."""
import re


def setup_filename(version: str) -> str:
    version = version.removeprefix('v')
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*)?', version):
        raise ValueError(f'Invalid release version: {version!r}')
    channel = '' if '-' in version else '-release'
    return f'Alp-toolbox-setup-v{version}{channel}.exe'
