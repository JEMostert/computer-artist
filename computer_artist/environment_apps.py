"""Applications, browsers and logs inside a running private environment."""

import os
import shutil
import socket
import time
from pathlib import Path

from .environments import DEFAULTS, exchange, kind, locations, route, status, stored, tail

CHROMIUM_PROFILES = {
    'chromium': '.config/chromium',
    'google-chrome': '.config/google-chrome',
    'google-chrome-stable': '.config/google-chrome',
    'vivaldi': '.config/vivaldi',
    'vivaldi-stable': '.config/vivaldi',
    'brave': '.config/BraveSoftware/Brave-Browser',
    'brave-browser': '.config/BraveSoftware/Brave-Browser',
    'microsoft-edge': '.config/microsoft-edge',
    'microsoft-edge-stable': '.config/microsoft-edge',
}
# Regenerable or instance-bound browser data; copying it wastes space or breaks locks.
PROFILE_SKIP = {
    'Cache',
    'Code Cache',
    'GPUCache',
    'GrShaderCache',
    'ShaderCache',
    'DawnCache',
    'DawnGraphiteCache',
    'DawnWebGPUCache',
    'CacheStorage',
    'Crashpad',
    'component_crx_cache',
    'optimization_guide_model_store',
    'SingletonLock',
    'SingletonSocket',
    'SingletonCookie',
    'cache2',
    'startupCache',
    'thumbnails',
    'lock',
    '.parentlock',
}


def windows(socket_path):
    from .client import Client

    with Client(socket_path, lane='host', deadline=5, action_budget=4) as client:
        return client.request('windows')['windows']


def launch(name, command, *, on_host=False, cwd=None, env=None, wait_window=None,
           window_name=None, timeout=60, wait_exit=False, settle=1.0):  # fmt: skip
    """Start an application and report whether it is alive, exited or showed a window."""
    from .client import Client

    info = route(name)
    _, runtime = locations(name)
    if window_name is not None and wait_window is None:
        wait_window = ''
    with Client(info['socket'], lane='host', deadline=timeout + 10, action_budget=100000) as client:
        before = {w['id'] for w in client.request('windows')['windows']}
        request = {'op': 'exec', 'argv': command, 'on_host': on_host, 'cwd': cwd, 'env': env or {}}
        app = exchange(runtime, request)['app']
        began, window = time.monotonic(), None
        while True:
            app = exchange(runtime, {'op': 'app', 'app': app['id']})['app']
            if wait_window is not None:
                current = client.request('windows')['windows']
                window = next(
                    (
                        w
                        for w in current
                        if w['id'] not in before
                        and wait_window.casefold() in w.get('title', '').casefold()
                    ),
                    None,
                )
                if window:
                    break
            code = app['returncode']
            # A launcher may exit 0 and leave its window to another process.
            if code is not None and (code != 0 or wait_window is None):
                break
            if code is None and wait_window is None and not wait_exit:
                if time.monotonic() - began >= settle:
                    break
            if time.monotonic() - began >= timeout:
                reason = 'window did not appear' if wait_window is not None else 'still running'
                raise TimeoutError(
                    f'{reason} after {timeout:g} s; app {app["id"]} alive={app["alive"]}; '
                    f'log {app["log"]}:\n' + '\n'.join(tail(app['log']))
                )
            time.sleep(0.2)
        result = {'ok': True, 'name': name, 'app': app}
        if app['returncode'] is not None:
            result['log_tail'] = tail(app['log'], 40)
            if app['returncode'] != 0:
                result.update(ok=False, error=f'Application exited with status {app["returncode"]}')
            elif not wait_exit and not window:
                result['note'] = 'Exited during startup; a launcher may have handed off its window'
        if window:
            fields = ('id', 'title', 'x', 'y', 'width', 'height', 'native', 'role')
            result['window'] = {k: window.get(k) for k in fields}
            if window_name:
                from .workspace import Workspace

                store = Workspace(info['window_dir'], info['output_dir'])
                current = client.request('windows')['windows']
                result['binding'] = store.assign(window['id'], window_name, current)
    return result


def launch_apps(name, definition):
    results = []
    for app in definition.get('apps', []):
        result = launch(
            name,
            app['command'],
            on_host=app['on_host'],
            cwd=app['cwd'],
            env=app['env'],
            wait_window=app['wait_window'],
            window_name=app['name'],
            timeout=app['timeout'],
        )
        results.append(result)
        if not result['ok']:
            raise RuntimeError(
                f'Startup app {app["command"]} failed: {result.get("error")}; '
                + '\n'.join(result.get('log_tail', []))
            )
    return results


def default_profile(family, executable):
    home = Path.home()
    if family == 'firefox':
        ini = home / '.mozilla/firefox/profiles.ini'
        if ini.is_file():
            import configparser

            parser = configparser.RawConfigParser()
            parser.read(ini)
            installs = [
                parser[s].get('Default') for s in parser.sections() if s.startswith('Install')
            ]
            paths = installs or [
                parser[s].get('Path') for s in parser.sections() if parser[s].get('Default') == '1'
            ]
            for path in paths:
                if path:
                    return (ini.parent / path) if not Path(path).is_absolute() else Path(path)
        raise ValueError('No default Firefox profile found; pass --copy-profile PATH')
    relative = CHROMIUM_PROFILES.get(Path(executable).name)
    if not relative:
        raise ValueError(f'Unknown default profile for {executable}; pass --copy-profile PATH')
    return home / relative


def profile_in_use(source, family):
    lock = source / ('SingletonLock' if family == 'chromium' else 'lock')
    if not lock.is_symlink():
        return False
    target = os.readlink(lock)
    # Chromium: HOSTNAME-PID; Firefox: IP:+PID. A lock from another host is in use.
    host, _, pid = target.rpartition('-' if family == 'chromium' else '+')
    if family == 'chromium' and host != socket.gethostname():
        return True
    try:
        os.kill(int(pid), 0)
        return True
    except (ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True


def copy_profile(source, destination, family):
    source = Path(source).expanduser().resolve()
    if not source.is_dir():
        raise ValueError(f'Browser profile does not exist: {source}')
    if profile_in_use(source, family):
        raise ValueError(
            f'Source browser profile {source} is in use; close that browser before copying '
            '(live profiles are never copied or shared)'
        )
    copied = {'files': 0, 'bytes': 0}

    def skip(folder, names):
        ignored = {n for n in names if n in PROFILE_SKIP}
        for n in names:
            if n not in ignored:
                path = Path(folder) / n
                if path.is_file() and not path.is_symlink():
                    copied['files'] += 1
                    copied['bytes'] += path.stat().st_size
        return ignored

    shutil.copytree(source, destination, symlinks=True, ignore=skip)
    destination.chmod(0o700)
    return {'source': str(source), **copied}


def browser(args):
    route(args.name)  # Refuse before copying anything when not running.
    state, _ = locations(args.name)
    definition = stored(args.name) or DEFAULTS
    on_host = args.on_host or kind(definition) == 'host'
    if on_host:
        executable = shutil.which(args.executable)
        if not executable:
            raise ValueError(f'Browser executable not found on the host: {args.executable}')
    else:
        executable = args.executable
    if args.url.startswith('-'):
        raise ValueError('Browser URL cannot begin with a dash')
    label = args.profile or Path(args.executable).name
    from .files import key

    profile = state / 'browsers' / key(label)
    copied = None
    if args.copy_profile:
        if profile.exists():
            if not args.replace_profile:
                raise ValueError(
                    f'Profile {profile} already exists; reuse it, choose --profile NAME or pass --replace-profile'
                )
            shutil.rmtree(profile)
        source = (
            default_profile(args.family, args.executable)
            if args.copy_profile == 'default'
            else args.copy_profile
        )
        copied = copy_profile(source, profile, args.family)
    profile.mkdir(exist_ok=True, mode=0o700)
    if args.family == 'firefox':
        command = [executable, '--new-instance', '--no-remote', '--profile', str(profile)]
    else:
        # Basic storage avoids a wallet prompt; logins encrypted by a wallet may need signing in again.
        command = [executable, '--ozone-platform=wayland', '--user-data-dir=' + str(profile),
                   '--no-first-run', '--no-default-browser-check', '--password-store=basic']  # fmt: skip
    command += args.browser_args or []
    command.append(args.url)
    result = launch(
        args.name,
        command,
        on_host=on_host,
        wait_window='',
        window_name=args.window_name,
        timeout=args.timeout,
    )
    result['profile'] = str(profile)
    if copied:
        result['copied_profile'] = {
            **copied,
            'note': 'Independent copy; wallet-encrypted logins may need signing in again',
        }
    return result


# Diagnostics


def logs(name, source, lines):
    """List or tail logs; a1, a2… name this session's apps, older ones keep their file stem."""
    state, _ = locations(name)
    folder = state / 'logs'
    files = {k: folder / f'{k}.log' for k in ('kwin', 'build', 'podman')}
    current = {a['id']: Path(a['log']) for a in status(name).get('applications', [])}
    files.update(current)
    older = sorted(
        (p for p in (folder / 'apps').glob('*.log') if p not in current.values()), reverse=True
    )
    if not source:
        available = {k: str(v) for k, v in files.items() if v.exists()}
        return {
            'ok': True,
            'name': name,
            'logs': available,
            'previous': [p.stem for p in older[:20]],
        }
    files.update((p.stem, p) for p in older)
    if source not in files or not files[source].exists():
        raise ValueError(f'Unknown log {source!r}; run ca env logs {name} to list them')
    return {
        'ok': True,
        'name': name,
        'log': str(files[source]),
        'lines': tail(files[source], lines),
    }
