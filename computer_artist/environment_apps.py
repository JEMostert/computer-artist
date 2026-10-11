"""Applications, browsers and logs inside a running private environment."""

import shutil
import time
from pathlib import Path

from .environments import DEFAULTS, exchange, kind, locations, route, status, stored, tail


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


def browser(args):
    route(args.name)  # Refuse when the environment is not running.
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
