"""Writable workspace defaults for source checkouts and installed commands."""

import os
from pathlib import Path


def checkout_root():
    root = Path(__file__).resolve().parents[1]
    return root if (root / 'ca').is_file() and (root / 'plugin/CMakeLists.txt').is_file() else None


def default_window_dir():
    if os.environ.get('CA_WINDOW_DIR'):
        return Path(os.environ['CA_WINDOW_DIR']).expanduser()
    root = checkout_root()
    if root:
        return root / 'window'
    return (
        Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share')
        / 'computer-artist/window'
    )


def default_output_dir(window_root):
    if os.environ.get('CA_OUTPUT_DIR'):
        return Path(os.environ['CA_OUTPUT_DIR']).expanduser()
    if (
        checkout_root()
        or os.environ.get('CA_WINDOW_DIR')
        or Path(window_root).resolve() != default_window_dir().resolve()
    ):
        return Path(window_root).parent / 'output'
    return (
        Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state')
        / 'computer-artist/output'
    )


def skill_source():
    root = checkout_root()
    if root:
        return root / 'skills/computer-artist'
    from importlib.metadata import PackageNotFoundError, distribution

    try:
        package = distribution('computer-artist')
        for entry in package.files or ():
            if str(entry).endswith('share/computer-artist/skills/computer-artist/SKILL.md'):
                return Path(package.locate_file(entry)).resolve().parent
    except PackageNotFoundError:
        pass
    import sys

    return Path(sys.prefix) / 'share/computer-artist/skills/computer-artist'


def socket_path(explicit=None):
    """Resolve the compositor endpoint for CLI and read-only diagnostics."""
    if explicit:
        return explicit
    if os.environ.get('CA_SOCKET'):
        return os.environ['CA_SOCKET']
    runtime = os.environ.get('XDG_RUNTIME_DIR')
    if not runtime:
        raise ValueError('Set CA_SOCKET or pass --socket; XDG_RUNTIME_DIR is unavailable')
    return str(Path(runtime) / 'computer-artist/control')
