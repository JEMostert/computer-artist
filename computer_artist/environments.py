"""Named private agent desktops: definitions, recipes and lifecycle.

An environment is a stored definition plus, while running, one systemd user
service owning a private KWin, a private D-Bus session bus and a manager socket.
Container applications share one rootless Podman container per environment. It
sees the private desktop's display and bus sockets plus the folders the definition
mounts at their host paths. Nothing here falls back to the user's own desktop.
"""

import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from xml.sax.saxutils import escape

NAME = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,47}')
MEMORY = re.compile(r'[0-9]+(\.[0-9]+)?[bkmgBKMG]?')
ENV_KEY = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
DEFAULTS = {
    'width': 1600,
    'height': 1000,
    'xwayland': False,
    'image': None,
    'containerfile': None,
    'context': None,
    'mounts': [],
    'workdir': None,
    'env': {},
    'cpus': None,
    'memory': None,
    'gpu': False,
    'network': 'private',
    'apps': [],
}
CONTAINER_KEYS = ('mounts', 'workdir', 'cpus', 'memory', 'gpu')
# Toolkits otherwise pick X11 or a missing portal; keep apps on the private display.
APP_ENV = {
    'XDG_SESSION_TYPE': 'wayland',
    'QT_QPA_PLATFORM': 'wayland',
    'GDK_BACKEND': 'wayland',
    'MOZ_ENABLE_WAYLAND': '1',
    'ELECTRON_OZONE_PLATFORM_HINT': 'wayland',
}
SERVICES = {
    'portal': 'org.freedesktop.portal.Desktop',
    'notifications': 'org.freedesktop.Notifications',
    'secrets': 'org.freedesktop.secrets',
    'accessibility': 'org.a11y.Bus',
}


def check_name(name):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError(
            'Environment name must be 1–48 letters, digits, underscores or hyphens '
            'and start with a letter or digit'
        )
    return name


def state_root():
    # Not XDG_STATE_HOME: applications inside an environment see a private one.
    explicit = os.environ.get('CA_ENVIRONMENTS_DIR')
    return (
        Path(explicit).expanduser()
        if explicit
        else Path.home() / '.local/state/computer-artist/environments'
    )


def host_runtime():
    runtime = os.environ.get('CA_HOST_RUNTIME_DIR') or os.environ.get('XDG_RUNTIME_DIR')
    if not runtime:
        raise ValueError('XDG_RUNTIME_DIR is required')
    return Path(runtime)


def locations(name):
    check_name(name)
    return state_root() / name, host_runtime() / 'ca-environments' / name


def unit(name):
    check_name(name)
    return 'computer-artist-env-' + hashlib.sha256(name.encode()).hexdigest()[:16] + '.service'


def container_name(name):
    return 'ca-env-' + check_name(name)


def image_tag(name):
    return 'localhost/computer-artist-env-' + check_name(name).lower() + ':latest'


def host_environment():
    """Environment for podman/systemctl: always the user's real session, never a private one."""
    keep = ('HOME', 'USER', 'LOGNAME', 'PATH', 'LANG', 'LC_ALL', 'SHELL')
    env = {k: v for k, v in os.environ.items() if k in keep or k.startswith('LC_')}
    runtime = host_runtime()
    env['XDG_RUNTIME_DIR'] = str(runtime)
    if (runtime / 'bus').exists():
        env['DBUS_SESSION_BUS_ADDRESS'] = f'unix:path={runtime / "bus"}'
    return env


def run(command, timeout=None, **options):
    return subprocess.run(
        command, capture_output=True, text=True, env=host_environment(), timeout=timeout, **options
    )


# Definitions and recipes


def parse_mount(spec, base):
    """Accept SOURCE[:TARGET][:ro|rw]; the target defaults to the same host path."""
    if isinstance(spec, dict):
        source, target, mode = spec.get('source'), spec.get('target'), spec.get('mode', 'rw')
    elif isinstance(spec, str) and spec:
        parts = spec.split(':')
        mode = parts.pop() if len(parts) > 1 and parts[-1] in ('ro', 'rw') else 'rw'
        if len(parts) > 2:
            raise ValueError(f'Mount {spec!r} must be SOURCE[:TARGET][:ro|rw]')
        source, target = parts[0], parts[1] if len(parts) == 2 else None
    else:
        raise ValueError(f'Invalid mount: {spec!r}')
    if not isinstance(source, str) or not source or mode not in ('ro', 'rw'):
        raise ValueError(f'Invalid mount: {spec!r}')
    source = (Path(base) / Path(source).expanduser()).resolve()
    if not source.exists():
        raise ValueError(f'Mount source does not exist: {source}')
    target = str(source) if not target else target
    if not Path(target).is_absolute() or '..' in Path(target).parts:
        raise ValueError(f'Mount target must be an absolute path: {target!r}')
    return {'source': str(source), 'target': target, 'mode': mode}


def parse_app(app, base):
    if isinstance(app, (str, list)):
        app = {'command': app}
    if not isinstance(app, dict):
        raise ValueError(f'Invalid app: {app!r}')
    unknown = set(app) - {'command', 'on_host', 'cwd', 'env', 'wait_window', 'name', 'timeout'}
    if unknown:
        raise ValueError(f'Unknown app keys: {sorted(unknown)}')
    command = app.get('command')
    command = shlex.split(command) if isinstance(command, str) else command
    if (
        not isinstance(command, list)
        or not command
        or any(not isinstance(a, str) or '\0' in a for a in command)
    ):
        raise ValueError('App command must be a nonempty string or argument list')
    wait = app.get('wait_window')
    if wait is True:
        wait = ''
    if wait is not None and wait is not False and not isinstance(wait, str):
        raise ValueError('wait_window must be a title substring or true')
    timeout = app.get('timeout', 60)
    if not isinstance(timeout, (int, float)) or not 0 < timeout <= 900:
        raise ValueError('App timeout must be 0–900 seconds')
    name = app.get('name')
    if name is not None:
        from .files import key

        key(name)
    return {
        'command': command,
        'on_host': bool(app.get('on_host', False)),
        'cwd': str((Path(base) / app['cwd']).resolve()) if app.get('cwd') else None,
        'env': check_env(app.get('env', {})),
        'wait_window': None if wait is False else wait,
        'name': name,
        'timeout': timeout,
    }


def check_env(values):
    if not isinstance(values, dict):
        raise ValueError('env must be a table of NAME = "value" strings')
    for k, v in values.items():
        if not ENV_KEY.fullmatch(str(k)) or not isinstance(v, str) or '\0' in v:
            raise ValueError(f'Invalid environment variable: {k!r}')
    return dict(values)


def normalize(data, base, current=None):
    """Validate recipe/flag values and merge them over an existing definition."""
    allowed = set(DEFAULTS) | {'project'}
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f'Unknown environment keys: {sorted(unknown)}; allowed: {sorted(allowed)}')
    result = json.loads(json.dumps(current or DEFAULTS))
    base = Path(base)
    for k in ('width', 'height'):
        if k in data:
            if type(data[k]) is not int:
                raise ValueError(f'{k} must be an integer')
            result[k] = data[k]
    if not 320 <= result['width'] <= 7680 or not 200 <= result['height'] <= 4320:
        raise ValueError('Desktop size must be 320–7680 by 200–4320')
    for k in ('xwayland', 'gpu'):
        if k in data:
            if not isinstance(data[k], bool):
                raise ValueError(f'{k} must be true or false')
            result[k] = data[k]
    if data.get('image') and data.get('containerfile'):
        raise ValueError('Use either image or containerfile, not both')
    if data.get('image'):
        if not isinstance(data['image'], str) or data['image'].startswith('-'):
            raise ValueError('image must be an image reference')
        result.update(image=data['image'], containerfile=None, context=None)
    if data.get('containerfile'):
        file = (base / Path(data['containerfile']).expanduser()).resolve()
        if not file.is_file():
            raise ValueError(f'Containerfile does not exist: {file}')
        context = (
            (base / Path(data['context']).expanduser()).resolve()
            if data.get('context')
            else file.parent
        )
        if not context.is_dir():
            raise ValueError(f'Build context is not a directory: {context}')
        result.update(image=None, containerfile=str(file), context=str(context))
    if data.get('project'):
        mount = parse_mount(str(data['project']) + ':rw', base)
        result['mounts'] = [m for m in result['mounts'] if m['target'] != mount['target']]
        result['mounts'].append(mount)
        result['workdir'] = result['workdir'] or mount['target']
    for spec in data.get('mounts') or ():
        mount = parse_mount(spec, base)
        result['mounts'] = [m for m in result['mounts'] if m['target'] != mount['target']]
        result['mounts'].append(mount)
    if data.get('workdir'):
        if not Path(data['workdir']).is_absolute():
            raise ValueError('workdir must be an absolute path inside the container')
        result['workdir'] = data['workdir']
    if 'env' in data:
        result['env'] = {**result['env'], **check_env(data['env'])}
    if data.get('cpus') is not None:
        if not isinstance(data['cpus'], (int, float)) or not 0 < data['cpus'] <= 1024:
            raise ValueError('cpus must be a positive number')
        result['cpus'] = data['cpus']
    if data.get('memory') is not None:
        if not isinstance(data['memory'], str) or not MEMORY.fullmatch(data['memory']):
            raise ValueError('memory must look like 512m or 4g')
        result['memory'] = data['memory']
    if 'network' in data:
        if data['network'] not in ('private', 'host', 'none'):
            raise ValueError('network must be private, host or none')
        result['network'] = data['network']
    if 'apps' in data:
        if not isinstance(data['apps'], list):
            raise ValueError('apps must be a list')
        result['apps'] = [parse_app(app, base) for app in data['apps']]
    if not (result['image'] or result['containerfile']):
        if (
            any(result[k] not in (None, False, [], {}) for k in CONTAINER_KEYS)
            or result['network'] != 'private'
        ):
            raise ValueError(
                'mounts, workdir, cpus, memory, gpu and network require image or containerfile; '
                'host applications already see your files'
            )
    return result


def load_recipe(path):
    path = Path(path).expanduser().resolve()
    text = path.read_text()
    try:
        data = tomllib.loads(text) if path.suffix != '.json' else json.loads(text)
    except (tomllib.TOMLDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f'Invalid recipe {path}: {error}') from None
    if not isinstance(data, dict):
        raise ValueError('A recipe must be a TOML table or JSON object')
    return data, path.parent


def flag_values(args):
    values = {}
    for k in ('image', 'containerfile', 'context', 'project', 'workdir', 'cpus', 'memory'):
        if getattr(args, k, None) is not None:
            values[k] = (
                str(getattr(args, k))
                if k in ('containerfile', 'context', 'project')
                else getattr(args, k)
            )
    for k in ('width', 'height', 'network'):
        if getattr(args, k, None) is not None:
            values[k] = getattr(args, k)
    for k in ('xwayland', 'gpu'):
        if getattr(args, k, None) is not None:
            values[k] = getattr(args, k)
    if getattr(args, 'mount', None):
        values['mounts'] = args.mount
    if getattr(args, 'env', None):
        values['env'] = dict(item.split('=', 1) for item in args.env)
    return values


def stored(name):
    state, _ = locations(name)
    path = state / 'definition.json'
    if path.is_file():
        return json.loads(path.read_text())
    legacy = state / 'environment.json'
    if legacy.is_file():
        # Environments from the first prototype only recorded their desktop.
        config = json.loads(legacy.read_text())
        return normalize(
            {k: config[k] for k in ('width', 'height', 'xwayland') if k in config}, state
        )
    return None


def define(name, args, *, replace=False):
    """Store a definition: a recipe replaces it, then flags update it."""
    state, _ = locations(name)
    definition = None if replace else stored(name)
    provenance = definition.get('recipe') if definition else None
    if getattr(args, 'recipe', None):
        data, base = load_recipe(args.recipe)
        definition = normalize(data, base)
        provenance = str(Path(args.recipe).expanduser().resolve())
    flags = flag_values(args)
    if flags or definition is None:
        definition = normalize(flags, Path.cwd(), definition)
    definition.pop('recipe', None)
    if provenance:
        definition['recipe'] = provenance
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    from .files import atomic_json

    atomic_json(state / 'definition.json', definition)
    return definition


def kind(definition):
    return 'container' if definition.get('image') or definition.get('containerfile') else 'host'


# Running environments


def exchange(runtime, request, timeout=5):
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(timeout)
        connection.connect(str(runtime / 'manager'))
        connection.sendall(json.dumps(request).encode() + b'\n')
        with connection.makefile('rb') as stream:
            result = json.loads(stream.readline(4 * 1048576))
    if not result.get('ok'):
        raise RuntimeError(result.get('error', 'Environment manager rejected request'))
    return result


def status(name):
    state, runtime = locations(name)
    try:
        return exchange(runtime, {'op': 'status'})
    except (OSError, ValueError):
        definition = stored(name)
        return {
            'ok': True,
            'name': name,
            'running': False,
            'state': str(state),
            'kind': kind(definition) if definition else None,
            'defined': definition is not None,
        }


def route(name):
    info = status(name)
    if not info['running']:
        raise ValueError(f'Environment {name!r} is not running; use ca env start {name}')
    # Never route a named environment to a surviving or substituted host plugin.
    from .client import Client

    with Client(info['socket'], lane='host', deadline=5, action_budget=4) as client:
        capabilities = client.request('capabilities')
    if capabilities.get('environment') != name:
        raise ValueError('Environment plugin identity mismatch; refusing desktop input')
    return info


def plugin_source(explicit):
    from .config import checkout_root

    if explicit:
        path = Path(explicit).expanduser().resolve()
    elif checkout_root():
        path = checkout_root() / 'build/plugin/kwin/plugins/computerartist.so'
    else:
        path = Path('/usr/lib/qt6/plugins/kwin/plugins/computerartist.so')
    if not path.is_file():
        raise ValueError(
            f'Plugin missing: {path}; build it (scripts/build-plugin.sh) or pass --plugin'
        )
    return path


def ensure_image(name, definition, log, pull=False):
    """Build or pull the image, returning its reference; build layers are cached."""
    if definition.get('containerfile'):
        tag = image_tag(name)
        command = ['podman', 'build', '--tag', tag, '--file', definition['containerfile']]
        command += ['--pull=always'] if pull else []
        command.append(definition['context'])
    else:
        tag = definition['image']
        if not pull and run(['podman', 'image', 'exists', tag]).returncode == 0:
            return tag
        command = ['podman', 'pull', tag]
    with log.open('ab') as stream:
        stream.write(f'\n$ {shlex.join(command)}\n'.encode())
        stream.flush()
        result = subprocess.run(
            command, stdout=stream, stderr=subprocess.STDOUT, env=host_environment(), timeout=1800
        )
    if result.returncode:
        tail = log.read_text(errors='replace').splitlines()[-15:]
        raise RuntimeError(f'Image preparation failed ({log}):\n' + '\n'.join(tail))
    return tag


def gpu_devices():
    devices = []
    if Path('/dev/dri').is_dir():
        devices.append('/dev/dri')
    cdi = [Path('/etc/cdi'), Path('/var/run/cdi')]
    if any(p.is_dir() and any(p.glob('nvidia*.yaml')) for p in cdi):
        devices.append('nvidia.com/gpu=all')
    return devices


def container_command(name, definition, image, state, runtime, display=None):
    """Return the podman run argument list for an environment's application container."""
    shared = runtime / 'shared'
    home = state / 'home'
    env = {
        **APP_ENV,
        'XDG_RUNTIME_DIR': str(shared),
        'WAYLAND_DISPLAY': str(shared / 'wayland'),
        'DBUS_SESSION_BUS_ADDRESS': f'unix:path={shared / "bus"}',
        'HOME': str(home),
        'CA_ENVIRONMENT': name,
    }
    command = [
        'podman', 'run', '--detach', '--rm', '--replace', '--init',
        '--name', container_name(name),
        '--label', f'computer-artist.environment={name}',
        '--hostname', 'ca-' + name.lower().replace('_', '-'),
        '--userns=keep-id',
        '--security-opt', 'label=disable',
        '--stop-timeout', '3',
    ]  # fmt: skip
    # Same-path mounts keep every path an app reports valid on the host.
    for folder in (shared, home, state / 'browsers'):
        command += ['--volume', f'{folder}:{folder}']
    if display:
        command += ['--volume', '/tmp/.X11-unix:/tmp/.X11-unix:ro']
        env.update(DISPLAY=display, XAUTHORITY=str(shared / 'xauthority'))
    for mount in definition['mounts']:
        command += ['--volume', f'{mount["source"]}:{mount["target"]}:{mount["mode"]}']
    if definition['network'] != 'private':
        command.append('--network=' + definition['network'])
    if definition['cpus']:
        command += ['--cpus', str(definition['cpus'])]
    if definition['memory']:
        command += ['--memory', definition['memory']]
    if definition['gpu']:
        for device in gpu_devices():
            command += ['--device', device]
    command += ['--workdir', definition['workdir'] or str(home)]
    for k, v in {**env, **definition['env']}.items():
        command += ['--env', f'{k}={v}']
    # A plain sleep under --init exits on SIGTERM, so stopping never waits for a kill.
    command += ['--entrypoint', 'sleep', image, '2147483647']
    return command


def bus_config(path):
    return f"""<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <keep_umask/>
  <listen>unix:path={escape(str(path))}</listen>
  <auth>EXTERNAL</auth>
  <standard_session_servicedirs/>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
"""


def start(args):
    import fcntl

    name = args.name
    state, runtime = locations(name)
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state / 'manager.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        changes = bool(getattr(args, 'recipe', None) or flag_values(args))
        if status(name)['running']:
            if changes:
                raise ValueError(f'Environment {name!r} is running; stop it before changing it')
            return {**status(name), 'already_running': True}
        began = time.monotonic()
        definition = define(name, args) if changes or stored(name) is None else stored(name)
        container = kind(definition) == 'container'
        active = run(['systemctl', '--user', 'is-active', unit(name)])
        if active.stdout.strip() in ('active', 'activating', 'deactivating'):
            raise ValueError('Environment service is still starting or stopping; retry shortly')
        plugin = plugin_source(getattr(args, 'plugin', None))
        required = ['systemd-run', 'dbus-run-session', 'kwin_wayland'] + (
            ['podman'] if container else []
        )
        for command in required:
            if not shutil.which(command):
                raise ValueError(f'Required executable is missing: {command}')
        for folder in (
            'config',
            'cache',
            'data',
            'state',
            'window',
            'output',
            'logs/apps',
            'browsers',
            'home',
        ):
            (state / folder).mkdir(parents=True, exist_ok=True, mode=0o700)
        image = (
            ensure_image(name, definition, state / 'logs/build.log', getattr(args, 'pull', False))
            if container
            else None
        )
        shared = runtime / 'shared'
        shared.mkdir(parents=True, exist_ok=True, mode=0o700)
        runtime.chmod(0o700)
        shared.chmod(0o700)
        for stale in (
            'manager',
            'control',
            'shared/wayland',
            'shared/wayland.lock',
            'shared/bus',
            'shared/xauthority',
        ):
            (runtime / stale).unlink(missing_ok=True)
        log = state / 'logs/kwin.log'
        if log.exists() and log.stat().st_size > 4 * 1024 * 1024:
            log.replace(log.with_suffix('.log.1'))
        plugins = runtime / 'plugins/kwin/plugins'
        plugins.mkdir(parents=True, exist_ok=True)
        shutil.copy2(plugin, plugins / 'computerartist.so')
        (runtime / 'dbus.conf').write_text(bus_config(shared / 'bus'))
        (state / 'config/kwinrc').write_text(
            '[Plugins]\ncomputerartistEnabled=true\nnightlightEnabled=false\n[Wayland]\nInputMethod=\n'
        )
        config = {
            'name': name,
            'kind': kind(definition),
            'state': str(state),
            'runtime': str(runtime),
            'width': definition['width'],
            'height': definition['height'],
            'xwayland': definition['xwayland'],
            'container': container_name(name) if container else None,
            'image': image,
            'workdir': definition.get('workdir'),
            'host_runtime': str(host_runtime()),
            'started_at': time.time(),
        }
        (state / 'environment.json').write_text(json.dumps(config))
        # An explicit environment keeps the user's desktop/session variables out of
        # the compositor and everything it starts.
        keep = ('HOME', 'USER', 'LOGNAME', 'PATH', 'LANG', 'LC_ALL', 'SHELL')
        env = {k: v for k, v in os.environ.items() if k in keep or k.startswith('LC_')}
        env.update(
            XDG_RUNTIME_DIR=str(runtime),
            XDG_CONFIG_HOME=str(state / 'config'),
            XDG_CACHE_HOME=str(state / 'cache'),
            XDG_DATA_HOME=str(state / 'data'),
            XDG_STATE_HOME=str(state / 'state'),
            XDG_SESSION_TYPE='wayland',
            XDG_CURRENT_DESKTOP='KDE',
            # Chromium picks its Qt shim from this; without it KDE implies Qt 5 and aborts.
            KDE_SESSION_VERSION=os.environ.get('KDE_SESSION_VERSION') or kwin_major(),
            CA_PLUGIN_RUNTIME=str(runtime),
            CA_ENVIRONMENT=name,
            CA_HOST_RUNTIME_DIR=str(host_runtime()),
            QT_PLUGIN_PATH=str(runtime / 'plugins'),
            PYTHONPATH=str(Path(__file__).resolve().parents[1]),
            KWIN_USE_OVERLAYS='0',
        )
        command = [
            'systemd-run', '--user', '--quiet', '--collect', '--service-type=exec',
            '--unit=' + unit(name),
            '--description=Computer Artist environment ' + name,
            '--property=KillMode=control-group',
            '--property=TimeoutStopSec=10',
        ]  # fmt: skip
        if container:
            # Removes the container even if the manager or compositor dies.
            command.append(
                '--property=ExecStopPost=-/usr/bin/env XDG_RUNTIME_DIR='
                + str(host_runtime())
                + f' {shutil.which("podman")} rm --force --ignore --time 3 {container_name(name)}'
            )
        command += [
            '--', '/usr/bin/env', '-i', *[f'{k}={v}' for k, v in env.items()],
            'dbus-run-session', '--config-file=' + str(runtime / 'dbus.conf'), '--',
            sys.executable, '-m', 'computer_artist.environment_service', '_serve', str(state / 'environment.json'),
        ]  # fmt: skip
        result = run(command)
        if result.returncode:
            raise RuntimeError(result.stderr.strip())
        try:
            info = wait_ready(name, state)
            if container:
                start_container(name, definition, image, state, runtime, info.get('display'))
        except BaseException:
            stop(name)
            raise
        return {**status(name), 'startup_seconds': round(time.monotonic() - began, 2)}


def tail(path, lines=20):
    try:
        with open(path, 'rb') as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - 65536))
            return stream.read().decode(errors='replace').splitlines()[-lines:]
    except OSError:
        return []


def kwin_major():
    result = subprocess.run(['kwin_wayland', '--version'], capture_output=True, text=True)
    match = re.search(r'(\d+)\.\d+', result.stdout)
    return match.group(1) if match else '6'


def wait_ready(name, state, timeout=30):
    began = time.monotonic()
    while time.monotonic() - began < timeout:
        if status(name)['running']:
            return route(name)
        # The unit can exit before the manager ever answers; give systemd a moment.
        active = run(['systemctl', '--user', 'is-active', unit(name)]).stdout.strip()
        if active in ('failed', 'inactive') and time.monotonic() - began > 2:
            break
        time.sleep(0.1)
    log = state / 'logs/kwin.log'
    raise RuntimeError(
        f'Environment desktop failed to start; see {log} and journalctl --user -u {unit(name)}\n'
        + '\n'.join(tail(log, 12))
    )


def start_container(name, definition, image, state, runtime, display):
    command = container_command(name, definition, image, state, runtime, display)
    with (state / 'logs/podman.log').open('a') as log:
        log.write(f'\n$ {shlex.join(command)}\n')
    result = run(command, timeout=120)
    if result.returncode:
        raise RuntimeError('Container failed to start: ' + result.stderr.strip()[-2000:])
    probe = run(
        ['podman', 'container', 'inspect', '--format', '{{.State.Running}}', container_name(name)]
    )
    if probe.stdout.strip() != 'true':
        raise RuntimeError('Container exited immediately; the image needs a sleep executable')


def stop(name, timeout=20):
    state, runtime = locations(name)
    was_running = status(name)['running']
    result = run(['systemctl', '--user', 'stop', unit(name)], timeout=timeout + 10)
    leftovers = []
    if (
        shutil.which('podman')
        and run(['podman', 'container', 'exists', container_name(name)]).returncode == 0
    ):
        run(['podman', 'rm', '--force', '--time', '3', container_name(name)], timeout=30)
        if run(['podman', 'container', 'exists', container_name(name)]).returncode == 0:
            leftovers.append(container_name(name))
    active = run(['systemctl', '--user', 'is-active', unit(name)]).stdout.strip()
    if active in ('active', 'activating', 'deactivating'):
        leftovers.append(unit(name))
    if leftovers:
        raise RuntimeError(f'Stop incomplete; still present: {leftovers}. {result.stderr.strip()}')
    return {
        'ok': True,
        'name': name,
        'running': False,
        'stopped': was_running,
        'evidence': str(state / 'output'),
        'logs': str(state / 'logs'),
    }


def remove(name, confirmed):
    state, _ = locations(name)
    if status(name)['running']:
        raise ValueError(f'Environment {name!r} is running; stop it first')
    if not state.exists():
        raise ValueError(f'Environment {name!r} does not exist')
    if not confirmed:
        raise ValueError(
            f'Removing deletes {state} including browser profiles, window maps and evidence; '
            'pass --yes to confirm'
        )
    shutil.rmtree(state)
    if shutil.which('podman'):
        run(['podman', 'image', 'rm', '--ignore', image_tag(name)])
    return {'ok': True, 'name': name, 'removed': str(state)}


def doctor(name):
    checks, warnings = [], []

    def check(label, passed, detail, remedy=None):
        checks.append(
            {'name': label, 'passed': bool(passed), 'detail': detail}
            | ({'remedy': remedy} if remedy and not passed else {})
        )

    state, runtime = locations(name)
    definition = stored(name)
    check(
        'defined', definition is not None, str(state / 'definition.json'), f'ca env create {name} …'
    )
    definition = definition or DEFAULTS
    container = kind(definition) == 'container'
    for command in ['systemd-run', 'dbus-run-session', 'dbus-send', 'kwin_wayland'] + (
        ['podman'] if container else []
    ):
        check('executable_' + command, shutil.which(command), shutil.which(command) or 'missing')
    try:
        check('plugin', True, str(plugin_source(None)))
    except ValueError as error:
        check('plugin', False, str(error), 'Run scripts/build-plugin.sh')
    if container and shutil.which('podman'):
        if definition.get('image'):
            present = run(['podman', 'image', 'exists', definition['image']]).returncode == 0
            check(
                'image', True, definition['image'] + ('' if present else ' (pulled on first start)')
            )
        else:
            check(
                'containerfile',
                Path(definition['containerfile']).is_file(),
                definition['containerfile'],
            )
        for mount in definition['mounts']:
            check('mount', Path(mount['source']).exists(), mount)
    devices = gpu_devices()
    if definition.get('gpu'):
        check('gpu', devices, devices or 'No /dev/dri or CDI GPU specification')
    if shutil.which('nvidia-smi') and 'nvidia.com/gpu=all' not in devices:
        warnings.append(
            {
                'code': 'nvidia_without_cdi',
                'detail': 'Containers get Mesa/DRI only; install nvidia-container-toolkit and generate a CDI spec for CUDA',
            }
        )
    warnings.append(
        {'code': 'no_private_audio', 'detail': 'Environments provide no audio server or microphone'}
    )
    info = status(name)
    report = {'ok': True, 'name': name, 'running': info['running'], 'kind': kind(definition)}
    if info['running']:
        try:
            route(name)
            check('plugin_identity', True, info['socket'])
        except Exception as error:
            check('plugin_identity', False, str(error))
        if container:
            state_text = run(
                [
                    'podman',
                    'container',
                    'inspect',
                    '--format',
                    '{{.State.Status}}',
                    container_name(name),
                ]
            ).stdout.strip()
            check(
                'container',
                state_text == 'running',
                state_text or 'missing',
                f'ca env stop {name} && ca env start {name}',
            )
        services = activatable(runtime / 'shared/bus')
        report['services'] = {label: SERVICES[label] in services for label in SERVICES}
        if not report['services']['portal']:
            warnings.append(
                {
                    'code': 'no_portal',
                    'detail': 'org.freedesktop.portal.Desktop is not activatable on the private bus',
                }
            )
        report['apps'] = info.get('applications', [])
        for app in report['apps']:
            if app['returncode'] not in (None, 0):
                warnings.append(
                    {
                        'code': 'app_exited',
                        'app': app['id'],
                        'returncode': app['returncode'],
                        'log': app['log'],
                    }
                )
    report.update(ok=all(c['passed'] for c in checks), checks=checks, warnings=warnings)
    return report


def activatable(bus):
    result = subprocess.run(
        ['dbus-send', f'--bus=unix:path={bus}', '--print-reply', '--dest=org.freedesktop.DBus',
         '/org/freedesktop/DBus', 'org.freedesktop.DBus.ListActivatableNames'],
        capture_output=True, text=True, timeout=5,
    )  # fmt: skip
    return set(re.findall(r'string "([^"]+)"', result.stdout))
