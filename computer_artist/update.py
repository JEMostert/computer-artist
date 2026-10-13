"""Update a source checkout: pull, reinstall, rebuild and reload the KWin plugin."""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .client import Client
from .config import checkout_root, socket_path
from .diagnostics import plugin_source_hash

KWIN = ('qdbus6', 'org.kde.KWin', '/Plugins')


def add_command(commands):
    parser = commands.add_parser(
        'update',
        help='Update the source checkout and platform backend (KDE: rebuild/reload plugin)',
    )
    parser.add_argument('--check', action='store_true', help='Report what would change only')
    parser.add_argument('--no-pull', action='store_true', help='Use the checkout as it is')
    parser.add_argument(
        '--no-reload',
        action='store_true',
        help='Install the plugin for the next login; keep the running one loaded',
    )
    parser.add_argument(
        '--force', action='store_true', help='Reload even while a session holds an input lane'
    )


def run(*command, quiet=False):
    """Run a command; long output goes to stderr so stdout stays JSON."""
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE if quiet else sys.stderr,
        stderr=subprocess.PIPE if quiet else sys.stderr,
        text=True,
    )
    if result.returncode:
        detail = (result.stderr or '').strip() if quiet else 'see output above'
        raise RuntimeError(f'{" ".join(command)} failed ({detail})')
    return (result.stdout or '').strip() if quiet else ''


def git(root, *args):
    return run('git', '-C', str(root), *args, quiet=True)


def privileged(*command, directory):
    # The plugin directory is normally root-owned; sudo prompts on the terminal if needed.
    writable = os.access(directory, os.W_OK)
    run(*(() if writable else ('sudo',)), *command)


def plugin_directory():
    if os.environ.get('CA_KWIN_PLUGIN_DIR'):
        return Path(os.environ['CA_KWIN_PLUGIN_DIR'])
    multiarch = getattr(sys.implementation, '_multiarch', '')
    for qt in ('/usr/lib/qt6', '/usr/lib64/qt6', f'/usr/lib/{multiarch}/qt6'):
        directory = Path(qt) / 'plugins/kwin/plugins'
        if directory.is_dir():
            return directory
    raise ValueError('Cannot find the KWin plugin directory; set CA_KWIN_PLUGIN_DIR')


def stamped(path, source_hash):
    """Whether a built library carries this source hash (a UTF-16 QStringLiteral)."""
    try:
        return source_hash.encode('utf-16-le') in Path(path).read_bytes()
    except OSError:
        return False


def kwin(method, *args):
    return run(*KWIN, f'org.kde.KWin.Plugins.{method}', *args, quiet=True)


def loaded_plugins():
    return sorted(
        name for name in kwin('LoadedPlugins').split() if name.startswith('computerartist')
    )


def backend():
    """Loaded plugin build hash and whether another session holds a lane."""
    try:
        with Client(socket_path(), deadline=5, action_budget=4) as client:
            build = client.request('capabilities').get('build') or {}
            lanes = client.request('session_status').get('lanes', {})
    except (OSError, ValueError, ConnectionError) as error:
        # Unknown lane state counts as busy.
        return {'source_sha256': None, 'busy': None, 'error': str(error)}
    return {
        'source_sha256': build.get('source_sha256'),
        'busy': any(lane.get('busy') for lane in lanes.values()),
    }


def inspect(root):
    checkout = plugin_source_hash(root)
    directory = plugin_directory()
    loaded = loaded_plugins()
    login = directory / 'computerartist.so'
    versioned = f'computerartist-{checkout[:12]}'
    enabled = run(
        'kreadconfig6',
        '--file',
        'kwinrc',
        '--group',
        'Plugins',
        '--key',
        'computerartistEnabled',
        quiet=True,
    )
    return {
        'checkout': checkout,
        'directory': str(directory),
        'built': stamped(root / 'build/plugin/kwin/plugins/computerartist.so', checkout),
        'loaded_ids': loaded,
        'running': backend(),
        'versioned': versioned,
        'login_current': stamped(login, checkout),
        'login_enabled': enabled == 'true',
        'stale': sorted(
            path.name
            for path in directory.glob('computerartist-*.so')
            if path.stem not in loaded and path.stem != versioned
        ),
    }


def guard(running, force):
    if running['busy'] is not False and not force:
        cause = 'A session holds an input lane' if running['busy'] else running['error']
        raise ValueError(
            f'{cause}; finish it (ca session close) or pass --force, or use --no-reload to update at next login'
        )


def plan(state, *, reload=True, force=False):
    """Ordered actions needed to bring the plugin up to date with the checkout."""
    # Only swap a plugin KWin is running; never activate one the user left unloaded.
    reload_needed = (
        reload
        and bool(state['loaded_ids'])
        and state['running']['source_sha256'] != state['checkout']
    )
    if reload_needed:
        guard(state['running'], force)
    actions = []
    if not state['built'] and (reload_needed or not state['login_current']):
        actions.append('build')
    if reload_needed:
        actions += ['install_versioned', 'reload']
    if not state['login_current']:
        actions.append('install_login')
    if state['stale'] or reload_needed:
        # The replaced copy becomes stale once unloaded; KWin keeps its mapping.
        actions.append('prune')
    return actions


def pull(root):
    branch = git(root, 'rev-parse', '--abbrev-ref', 'HEAD')
    if git(root, 'status', '--porcelain', '--untracked-files=no'):
        raise ValueError(
            'The checkout has uncommitted changes; commit or stash them, or pass --no-pull'
        )
    before = git(root, 'rev-parse', 'HEAD')
    run('git', '-C', str(root), 'pull', '--ff-only', '--quiet')
    return {'branch': branch, 'from': before, 'to': git(root, 'rev-parse', 'HEAD')}


def reinstall(root, pulled):
    """Reinstall dependencies when pyproject.toml changed in the pulled range."""
    changed = subprocess.run(
        [
            'git',
            '-C',
            str(root),
            'diff',
            '--quiet',
            pulled['from'],
            pulled['to'],
            '--',
            'pyproject.toml',
        ],
    ).returncode
    if not changed:
        return 'unchanged'
    if sys.prefix == sys.base_prefix:
        # Never pip-install into a system interpreter.
        return (
            f"pyproject.toml changed; reinstall with your environment's pip: pip install -e {root}"
        )
    run(sys.executable, '-m', 'pip', 'install', '--quiet', '-e', str(root))
    return 'reinstalled'


def reload(state, force=False):
    """Swap the running plugin for the versioned build, restoring the old one on failure."""
    # A session may have started while the plugin was building.
    guard(backend(), force)
    old = state['loaded_ids']
    try:
        for name in old:
            kwin('UnloadPlugin', name)
        if kwin('LoadPlugin', state['versioned']) != 'true':
            raise RuntimeError(f'KWin refused to load {state["versioned"]}')
    except (OSError, RuntimeError) as error:
        restored = []
        for name in old:
            try:
                if name in loaded_plugins() or kwin('LoadPlugin', name) == 'true':
                    restored.append(name)
            except (OSError, RuntimeError):
                pass
        raise RuntimeError(f'{error}; running plugins now: {restored or "none"}') from None
    deadline = time.monotonic() + 5
    while backend()['source_sha256'] != state['checkout']:
        if time.monotonic() > deadline:
            raise RuntimeError(
                f'{state["versioned"]} is loaded but did not report the new build within 5 s; run ca doctor'
            )
        time.sleep(0.2)


def apply(root, state, actions, force=False):
    built = root / 'build/plugin/kwin/plugins/computerartist.so'
    directory = Path(state['directory'])
    if 'build' in actions:
        run(str(root / 'scripts/build-plugin.sh'))
        if not stamped(built, state['checkout']):
            raise RuntimeError('The built plugin does not carry the checkout source hash')
    if 'install_versioned' in actions:
        target = directory / f'{state["versioned"]}.so'
        # install replaces the file with a new inode, so mapped copies stay intact.
        privileged('install', '-m', '755', str(built), str(target), directory=directory)
    if 'reload' in actions:
        reload(state, force)
    if 'install_login' in actions:
        privileged(
            'install',
            '-m',
            '755',
            str(built),
            str(directory / 'computerartist.so'),
            directory=directory,
        )
    if 'prune' in actions:
        loaded = loaded_plugins()
        stale = [
            path
            for path in directory.glob('computerartist-*.so')
            if path.stem not in loaded and path.stem != state['versioned']
        ]
        if stale:
            privileged('rm', '-f', *map(str, stale), directory=directory)


def update(args):
    root = checkout_root()
    if root is None:
        raise ValueError(
            'ca update works on a source checkout; upgrade an installed wheel with pip instead'
        )
    result = {'ok': True, 'check': args.check, 'root': str(root)}
    if os.environ.get('CA_UPDATE_PULLED'):
        result['git'] = json.loads(os.environ.pop('CA_UPDATE_PULLED'))
    elif not args.no_pull and args.check:
        run('git', '-C', str(root), 'fetch', '--quiet')
        result['git'] = {'behind': int(git(root, 'rev-list', '--count', 'HEAD..@{u}'))}
    elif not args.no_pull:
        pulled = pull(root)
        pulled['python'] = reinstall(root, pulled)
        result['git'] = pulled
        if pulled['from'] != pulled['to']:
            # Continue with the freshly pulled update code.
            flags = [
                flag
                for flag in ('--no-reload', '--force')
                if vars(args)[flag[2:].replace('-', '_')]
            ]
            os.environ['CA_UPDATE_PULLED'] = json.dumps(pulled)
            os.execv(
                sys.executable, [sys.executable, str(root / 'ca'), 'update', '--no-pull', *flags]
            )
    if os.name == 'nt':
        result['backend'] = 'win32'
        result['plugin_actions'] = []
        if not args.check:
            from .diagnostics import diagnose
            from .setup import install

            installed = []
            for skills in (Path.home() / '.codex/skills', Path.home() / '.agents/skills'):
                destination = skills / 'computer-artist'
                if destination.is_symlink() or (destination / '.ca-install.json').is_file():
                    installed.append(
                        install(argparse.Namespace(skills_dir=skills, dry_run=False, remove=False))
                    )
            result['skills'] = installed
            doctor = diagnose(argparse.Namespace(**{**vars(args), 'window': None, 'build': False}))
            result['ok'] = doctor['ok']
            result['doctor'] = {
                'ok': doctor['ok'],
                'failed': [c['name'] for c in doctor['checks'] if not c['passed']],
            }
        return result
    state = inspect(root)
    actions = plan(state, reload=not args.no_reload, force=args.force)
    result['plugin'] = {**state, 'actions': actions}
    if args.check:
        return result
    apply(root, state, actions, args.force)
    if not state['loaded_ids']:
        result['note'] = 'The plugin is not loaded in KWin; nothing was reloaded.'
    elif not state['login_enabled']:
        result['note'] = (
            'KWin does not load the plugin at login; set computerartistEnabled=true in kwinrc [Plugins] if you want that.'
        )
    from .diagnostics import diagnose

    doctor = diagnose(argparse.Namespace(**{**vars(args), 'window': None, 'build': False}))
    result['doctor'] = {
        'ok': doctor['ok'],
        'failed': [check['name'] for check in doctor['checks'] if not check['passed']],
        'warnings': [warning['code'] for warning in doctor['warnings']],
    }
    result['ok'] = doctor['ok']
    return result
