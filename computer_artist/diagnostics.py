"""Read-only installation, backend and workspace diagnostics."""

import hashlib
import importlib.metadata
import os
import shutil
import sys
from pathlib import Path

from .client import Client
from .config import checkout_root, skill_source, socket_path
from .workspace import Workspace


def writable_destination(path):
    path = Path(path)
    while not path.exists() and path != path.parent:
        path = path.parent
    return path.is_dir() and os.access(path, os.W_OK | os.X_OK)


def plugin_source_hash(root):
    """Hash plugin sources exactly as the build stamps source_sha256; None without plugin/."""
    directory = Path(root) / 'plugin'
    if not directory.is_dir():
        return None
    names = sorted(
        path.name
        for path in directory.iterdir()
        if path.is_file()
        and (path.suffix in ('.cpp', '.h') or path.name in ('metadata.json', 'CMakeLists.txt'))
    )
    manifest = ''.join(
        f'{name}:{hashlib.sha256((directory / name).read_bytes()).hexdigest()}\n' for name in names
    )
    return hashlib.sha256(manifest.encode()).hexdigest()


def diagnose(args):
    store = Workspace(args.window_dir, args.output_dir)
    lane = getattr(args, 'lane', 'agent')
    environment = getattr(args, 'environment_info', None)
    checks = []
    warnings = []

    def check(name, passed, detail, remedy=None):
        checks.append(
            {
                'name': name,
                'passed': bool(passed),
                'detail': detail,
                **({'remedy': remedy} if remedy and not passed else {}),
            }
        )

    check(
        'python',
        sys.version_info >= (3, 12),
        sys.version.split()[0],
        'Install Python 3.12 or newer.',
    )
    try:
        importlib.import_module('PIL.Image')

        check('pillow', True, importlib.metadata.version('Pillow'))
    except ImportError:
        check(
            'pillow',
            False,
            'Pillow is unavailable',
            'Install the CLI with python -m pip install -e . in a virtual environment.',
        )
    for name, path in (('window_directory', store.root), ('output_directory', store.output)):
        check(
            name,
            writable_destination(path),
            str(path),
            'Choose a writable --window-dir / --output-dir or correct directory permissions.',
        )
    check(
        'agent_skill',
        (skill_source() / 'SKILL.md').is_file(),
        str(skill_source()),
        'Reinstall the CLI from the complete checkout.',
    )
    names = {}
    try:
        names = store.names()
        check('window_names', True, f'{len(names)} saved bindings')
    except (OSError, ValueError, TypeError) as error:
        check(
            'window_names',
            False,
            str(error),
            'Repair window/names.json from your backup before using saved names.',
        )
    broken_fragments = []
    scope = store.fragments.root / 'api-fragmants'
    try:
        folders = sorted(scope.iterdir()) if scope.exists() else []
    except OSError as error:
        folders = []
        broken_fragments.append({'directory': str(scope), 'error': str(error)})
    for folder in folders:
        if folder.name == 'trash' or not folder.is_dir():
            continue
        try:
            store.fragments.load(folder.name)
        except (OSError, ValueError, KeyError, TypeError) as error:
            broken_fragments.append({'name': folder.name, 'error': str(error)})
    check(
        'fragment_integrity',
        not broken_fragments,
        broken_fragments or 'All current source hashes match manifests',
        'Inspect ca fragments history/show; select a validated revision with ca fragments restore NAME --version VERSION, or recover from backup.',
    )
    backend = {}
    host_backend = {}
    windows = []
    selected = None
    socket = None
    try:
        socket = socket_path(args.socket)
        with Client(
            socket, deadline=min(args.deadline, 5), action_budget=8, window_dir=args.window_dir
        ) as client:
            backend = client.request('capabilities')
            raw_windows = client.request('windows')['windows']
        try:
            with Client(
                socket,
                lane='host',
                deadline=min(args.deadline, 5),
                action_budget=3,
                window_dir=args.window_dir,
            ) as host:
                host_backend = host.request('capabilities')
        except Exception as error:
            warnings.append({'code': 'host_lane_unavailable', 'error': str(error)})
        supported = backend.get('protocol', 0) >= 3 and 'capture' in backend.get('operations', [])
        check(
            'compositor',
            supported,
            {
                'socket': socket,
                'protocol': backend.get('protocol'),
                'backend': backend.get('backend'),
            },
            'Build and load the matching plugin using plugin/README.md; never overwrite a loaded library.',
        )
        if backend:
            plugin_build = backend.get('build')
            if plugin_build is None:
                check(
                    'plugin_build',
                    False,
                    'Loaded plugin predates build identity',
                    'Rebuild with ./scripts/build-plugin.sh and install/load it as docs/INSTALL.md describes.',
                )
            else:
                loaded = plugin_build.get('source_sha256')
                source_root = checkout_root()
                checkout = plugin_source_hash(source_root) if source_root else None
                if checkout is None:
                    # Installed packages carry no plugin sources to compare against.
                    check(
                        'plugin_build',
                        True,
                        {
                            'loaded': loaded,
                            'checkout': None,
                            'note': 'No checkout plugin/ directory; source hash comparison skipped',
                        },
                    )
                else:
                    check(
                        'plugin_build',
                        loaded == checkout,
                        {'loaded': loaded, 'checkout': checkout},
                        'The loaded plugin was built from different sources than this checkout. Rebuild with ./scripts/build-plugin.sh, install and reload it following docs/INSTALL.md; never overwrite a loaded library.',
                    )
                if plugin_build.get('kwin_headers') != plugin_build.get('kwin_running'):
                    warnings.append(
                        {
                            'code': 'plugin_kwin_mismatch',
                            'built_for': plugin_build.get('kwin_headers'),
                            'running': plugin_build.get('kwin_running'),
                            'remedy': 'Rebuild the plugin against the running KWin and reload it.',
                        }
                    )
        for window in raw_windows:
            labels = {
                'target_not_visible': 'Not visible on the current desktop',
                'surface_unavailable': 'Application surface is unavailable',
                'xwayland_unsupported': 'XWayland input is unsupported',
                'window_type_unsupported': 'Window type does not support acquisition',
                'screen_locked': 'Screen is locked',
                'seat_gesture_active': 'Human drag or touch sequence is active',
                'keyboard_keys_held': 'Release held keyboard keys before acquisition',
                'pointer_buttons_held': 'Release held pointer buttons before acquisition',
                'lane_busy': 'Input lane is already controlled',
                'other_lane_owns_application': 'Application is controlled by the other input lane',
                'application_popup_or_special_window': 'Application has an unsupported popup or special window',
                'human_pointer_in_application': 'Move the human pointer outside every window of this application',
                'human_keyboard_in_application': 'Move human keyboard focus outside this application',
                'pointer_resources_unavailable': 'Application has no usable Wayland pointer resources',
                'pointer_constrained': 'Application constrains the human pointer',
            }
            reason_codes = window.get('agent_restrictions', [])
            reasons = [labels.get(code, code) for code in reason_codes]
            if not window.get('native'):
                reasons.append('XWayland input is unsupported')
            if not window.get('visible'):
                reasons.append('Not visible on the current desktop')
            if window.get('human_active'):
                reasons.append(
                    'Move human pointer and keyboard focus outside this application before acquisition'
                )
            if window.get('host') or window.get('agent'):
                reasons.append('Already controlled by an input lane')
            if window.get('acquirable') is False and not reasons:
                reasons.append(
                    'Compositor refuses acquisition (window type, grab or pointer resource restriction)'
                )
            windows.append(
                {
                    **{
                        field: window.get(field)
                        for field in ('id', 'title', 'native', 'visible', 'pid')
                    },
                    'agent_candidate': not reasons,
                    'readiness_source': 'native_acquisition_checks'
                    if 'agent_restrictions' in window
                    else 'legacy_heuristic',
                    'restrictions': list(dict.fromkeys(reasons)),
                    'restriction_codes': reason_codes,
                    'host_candidate': window.get('host_acquirable'),
                    'host_restrictions': [
                        labels.get(code, code) for code in window.get('host_restrictions', [])
                    ],
                }
            )
        if any('agent_restrictions' not in window for window in raw_windows):
            warnings.append(
                {
                    'code': 'legacy_readiness',
                    'remedy': 'Loaded plugin lacks detailed acquisition readiness. Candidate assessment is approximate; isolated tested plugin updates require deliberate installation.',
                }
            )
        live_ids = {w['id'] for w in windows}
        stale = [name for name, identity in names.items() if identity not in live_ids]
        if stale:
            warnings.append(
                {
                    'code': 'stale_bindings',
                    'names': stale,
                    'remedy': 'Rebind deliberately with ca set --name NAME --title UNIQUE_TITLE, then revalidate the layout.',
                }
            )
        if args.window:
            identity = store.resolve_window(args.window.strip('{}'))
            selected = next((w for w in windows if w['id'] == identity), None)
            # Judge readiness for the lane the command would actually use.
            ready = selected and (
                selected['host_candidate'] if lane == 'host' else selected['agent_candidate']
            )
            check(
                'selected_window',
                bool(ready),
                {**selected, 'lane': lane} if selected else f'Window {identity} is not open',
                'Inspect ca windows and the reported restrictions; no host fallback is performed.',
            )
        if host_backend and not host_backend.get('clipboard'):
            warnings.append(
                {
                    'code': 'clipboard_unavailable',
                    'remedy': 'The loaded host plugin cannot paste text. Check the current host capabilities and rebuild/load deliberately if this workflow is needed.',
                }
            )
    except Exception as error:
        check(
            'compositor',
            False,
            {'socket': socket, 'error': str(error)},
            'Check CA_SOCKET / --socket, your KDE Wayland session, and plugin/README.md.',
        )
    build = None
    if args.build:
        build = {
            name: shutil.which(name)
            for name in (
                'cmake',
                'ninja',
                'c++',
                'pkg-config',
                'wayland-scanner',
                'qdbus6',
                'dbus-run-session',
                'kwin_wayland',
            )
        }
        check(
            'build_tools',
            all(build.values()),
            build,
            'Install the system build/integration dependencies listed in docs/INSTALL.md.',
        )
        root = checkout_root()
        check(
            'plugin_sources',
            root is not None,
            str(root) if root else 'Installed Python package',
            'Build the native plugin from a source checkout matching your installed KWin.',
        )
        check(
            'kwin_headers',
            Path('/usr/include/kwin/input.h').is_file(),
            '/usr/include/kwin/input.h',
            'Install the matching KWin development headers.',
        )
    return {
        'ok': all(c['passed'] for c in checks),
        'command': 'doctor',
        'lane': lane,
        'environment': environment,
        'checks': checks,
        'warnings': warnings,
        'backend': backend,
        'host_backend': host_backend,
        'windows': windows,
        'selected_window': selected,
        'build_tools': build,
        'note': 'Read-only diagnostics; candidates are not proof of application compatibility.',
    }
