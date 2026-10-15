"""Private environments on hosts without KWin: one Linux box container per environment.

On Windows and macOS, `ca env` runs the same private KWin desktop as on Linux, but
inside a Docker Desktop container built from containers/linux-box. Every
`ca --environment NAME ...` command runs inside that box through `exec`; paths in
its arguments and JSON output are translated between the host and the box. The
box never touches the user's own desktop, and a stopped box is an error, never a
fallback. Set CA_ENVIRONMENT_BACKEND=box to use the same path on Linux.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath

from .containers import MISSING, RUNTIMES, Runtime
from .environments import NAME, check_env, check_name, load_recipe

BOX_STATE = PurePosixPath('/ca/environments')
BOX_WINDOW = PurePosixPath('/ca/window')
PROJECT = PurePosixPath('/project')
DEFAULTS = {
    'width': 1600,
    'height': 1000,
    'xwayland': False,
    'containerfile': None,
    'context': None,
    'mounts': [],
    'workdir': None,
    'env': {},
    'cpus': None,
    'memory': None,
    'network': 'private',
    'runtime': None,
    'apps': [],
}
SOURCES = ('ca', 'pyproject.toml', 'containers/linux-box/Containerfile', '.dockerignore')
# Arguments holding host paths; positional ones are named by their argparse dest.
PATH_DESTS = ('trace', 'window_dir', 'output_dir', 'path_file', 'output', 'path')
# The Arch base is published for amd64 only; arm64 hosts (Apple silicon) emulate it.
PLATFORM = 'linux/amd64'


def docker_only():
    return os.name == 'nt' or sys.platform == 'darwin'


def enabled():
    return docker_only() or os.environ.get('CA_ENVIRONMENT_BACKEND') == 'box'


def root():
    if os.environ.get('CA_ENVIRONMENTS_DIR'):
        return Path(os.environ['CA_ENVIRONMENTS_DIR']).expanduser()
    base = os.environ.get('LOCALAPPDATA') or Path.home() / '.local/state'
    return Path(base) / 'computer-artist' / 'environments'


def folder(name):
    return root() / check_name(name)


def container(name):
    return 'ca-box-' + check_name(name)


def engine_for(definition=None):
    """CA_CONTAINER_RUNTIME or the definition's choice, else Docker, then Podman.

    Windows and macOS boxes use Docker Desktop only; Podman remains for Linux.
    """
    import shutil

    allowed = ('docker',) if docker_only() else ('docker', 'podman')
    preferred = (definition or {}).get('runtime') or os.environ.get('CA_CONTAINER_RUNTIME')
    if preferred:
        if preferred not in RUNTIMES:
            raise ValueError(f'Container runtime must be one of {RUNTIMES}')
        if preferred not in allowed:
            raise ValueError(
                'Windows and macOS environments use Docker Desktop; Podman is not supported'
            )
        if not shutil.which(preferred):
            raise RuntimeError(f'{preferred} was selected but is not installed. ' + MISSING)
        return Runtime(preferred)
    for name in allowed:
        if shutil.which(name):
            return Runtime(name)
    raise RuntimeError(MISSING)


# Definitions


def parse_mount(spec, base):
    """SOURCE[:TARGET][:ro|rw] where TARGET is a box path; drive letters stay in SOURCE."""
    if isinstance(spec, dict):
        source, target, mode = spec.get('source'), spec.get('target'), spec.get('mode', 'rw')
    elif isinstance(spec, str) and spec:
        mode = 'rw'
        if spec.endswith((':ro', ':rw')):
            spec, mode = spec[:-3], spec[-2:]
        split = spec.rfind(':/')
        source, target = (spec[:split], spec[split + 1 :]) if split > 1 else (spec, None)
    else:
        raise ValueError(f'Invalid mount: {spec!r}')
    if not isinstance(source, str) or not source or mode not in ('ro', 'rw'):
        raise ValueError(f'Invalid mount: {spec!r}')
    source = (Path(base) / Path(source).expanduser()).resolve()
    if not source.exists():
        raise ValueError(f'Mount source does not exist: {source}')
    target = PurePosixPath(target or f'/mnt/{source.name or "drive"}')
    if not target.is_absolute() or '..' in target.parts:
        raise ValueError(f'Mount target must be an absolute box path: {str(target)!r}')
    if ',' in str(source) or ',' in str(target):
        raise ValueError(f'Mount paths must not contain ",": {spec!r}')
    return {'source': str(source), 'target': str(target), 'mode': mode}


def parse_app(app):
    if isinstance(app, (str, list)):
        app = {'command': app}
    if not isinstance(app, dict):
        raise ValueError(f'Invalid app: {app!r}')
    unknown = set(app) - {'command', 'cwd', 'env', 'wait_window', 'name', 'timeout'}
    if unknown:
        raise ValueError(f'Unknown app keys in a box: {sorted(unknown)}')
    import shlex

    command = app.get('command')
    command = shlex.split(command) if isinstance(command, str) else command
    if not isinstance(command, list) or not command or any(not isinstance(a, str) for a in command):
        raise ValueError('App command must be a nonempty string or argument list')
    cwd = app.get('cwd')
    if cwd is not None and not PurePosixPath(cwd).is_absolute():
        raise ValueError('App cwd must be an absolute path inside the box')
    timeout = app.get('timeout', 60)
    if not isinstance(timeout, (int, float)) or not 0 < timeout <= 900:
        raise ValueError('App timeout must be 0–900 seconds')
    return {
        'command': command,
        'cwd': cwd,
        'env': check_env(app.get('env', {})),
        'wait_window': '' if app.get('wait_window') is True else app.get('wait_window'),
        'name': app.get('name'),
        'timeout': timeout,
    }


def normalize(data, base, current=None):
    allowed = set(DEFAULTS) | {'project'}
    unknown = set(data) - allowed
    if 'image' in unknown or 'gpu' in unknown:
        raise ValueError(
            'A Linux box uses its own image; extend it with a containerfile that starts with '
            '"ARG BOX_IMAGE" and "FROM ${BOX_IMAGE}". GPU passthrough is not supported.'
        )
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
    if 'xwayland' in data:
        if not isinstance(data['xwayland'], bool):
            raise ValueError('xwayland must be true or false')
        result['xwayland'] = data['xwayland']
    if data.get('containerfile'):
        file = (base / Path(data['containerfile']).expanduser()).resolve()
        if not file.is_file():
            raise ValueError(f'Containerfile does not exist: {file}')
        context = (base / Path(data['context'])).resolve() if data.get('context') else file.parent
        if not context.is_dir():
            raise ValueError(f'Build context is not a directory: {context}')
        result.update(containerfile=str(file), context=str(context))
    if data.get('project'):
        mount = parse_mount({'source': str(data['project']), 'target': str(PROJECT)}, base)
        result['mounts'] = [m for m in result['mounts'] if m['target'] != mount['target']]
        result['mounts'].append(mount)
        result['workdir'] = result['workdir'] or str(PROJECT)
    for spec in data.get('mounts') or ():
        mount = parse_mount(spec, base)
        result['mounts'] = [m for m in result['mounts'] if m['target'] != mount['target']]
        result['mounts'].append(mount)
    if data.get('workdir'):
        if not PurePosixPath(data['workdir']).is_absolute():
            raise ValueError('workdir must be an absolute path inside the box')
        result['workdir'] = data['workdir']
    if 'env' in data:
        result['env'] = {**result['env'], **check_env(data['env'])}
    if data.get('cpus') is not None:
        if not isinstance(data['cpus'], (int, float)) or not 0 < data['cpus'] <= 1024:
            raise ValueError('cpus must be a positive number')
        result['cpus'] = data['cpus']
    if data.get('memory') is not None:
        if not isinstance(data['memory'], str) or not re.fullmatch(
            r'[0-9.]+[bkmgBKMG]?', data['memory']
        ):
            raise ValueError('memory must look like 512m or 4g')
        result['memory'] = data['memory']
    if 'network' in data:
        if data['network'] not in ('private', 'host', 'none'):
            raise ValueError('network must be private, host or none')
        result['network'] = data['network']
    if data.get('runtime') is not None:
        if data['runtime'] not in RUNTIMES:
            raise ValueError(f'runtime must be one of {RUNTIMES}')
        result['runtime'] = data['runtime']
    if 'apps' in data:
        if not isinstance(data['apps'], list):
            raise ValueError('apps must be a list')
        result['apps'] = [parse_app(app) for app in data['apps']]
    return result


def flag_values(args):
    values = {}
    for k in ('containerfile', 'context', 'project'):
        if getattr(args, k, None) is not None:
            values[k] = str(getattr(args, k))
    for k in ('workdir', 'cpus', 'memory', 'width', 'height', 'network', 'runtime', 'xwayland'):
        if getattr(args, k, None) is not None:
            values[k] = getattr(args, k)
    if getattr(args, 'image', None):
        values['image'] = args.image  # Refused with an explanation by normalize.
    if getattr(args, 'gpu', None):
        values['gpu'] = True
    if getattr(args, 'mount', None):
        values['mounts'] = args.mount
    if getattr(args, 'env', None):
        values['env'] = dict(item.split('=', 1) for item in args.env)
    return values


def stored(name):
    path = folder(name) / 'box.json'
    return json.loads(path.read_text()) if path.is_file() else None


def define(name, args, *, replace=False):
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
    folder(name).mkdir(parents=True, exist_ok=True)
    from .files import atomic_json

    atomic_json(folder(name) / 'box.json', definition)
    return definition


# Images and containers


def checkout():
    from .config import checkout_root

    path = checkout_root()
    if path is None or not (path / 'containers/linux-box/Containerfile').is_file():
        raise ValueError('Linux boxes are built from a Computer Artist source checkout')
    return path


def base_tag(source):
    """Content-addressed tag: any change to the box's sources builds a new image."""
    digest = hashlib.sha256()
    files = [source / f for f in SOURCES]
    for top in ('computer_artist', 'plugin'):
        files += sorted(
            p for p in (source / top).rglob('*') if p.is_file() and '__pycache__' not in p.parts
        )
    for path in files:
        digest.update(path.relative_to(source).as_posix().encode() + b'\0')
        digest.update(path.read_bytes() + b'\0')
    return 'localhost/computer-artist-box:' + digest.hexdigest()[:16]


def build(command, log):
    command[2:2] = ['--platform', PLATFORM]
    with log.open('ab') as stream:
        stream.write(('\n$ ' + ' '.join(command) + '\n').encode())
        stream.flush()
        try:
            result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, timeout=3600)
        except FileNotFoundError:
            raise RuntimeError(MISSING) from None
        except subprocess.TimeoutExpired:
            raise RuntimeError(f'Box image build timed out; see {log}') from None
    if result.returncode:
        tail = log.read_text(errors='replace').splitlines()[-15:]
        raise RuntimeError(f'Box image build failed ({log}):\n' + '\n'.join(tail))


def ensure_image(name, definition, engine, log, pull=False):
    source = checkout()
    tag = base_tag(source)
    if pull or not engine.image_exists(tag):
        build(
            engine.build_command(
                tag, str(source / 'containers/linux-box/Containerfile'), str(source), pull=pull
            ),
            log,
        )
    if not definition.get('containerfile'):
        return tag
    derived = f'localhost/computer-artist-box-{name.lower()}:latest'
    command = engine.build_command(derived, definition['containerfile'], definition['context'])
    command[2:2] = ['--build-arg', f'BOX_IMAGE={tag}']
    build(command, log)
    return derived


def window_root():
    from .config import default_window_dir

    path = default_window_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def mapping(definition):
    """Host path ↔ box path pairs, most specific first."""
    pairs = [(root().resolve(), BOX_STATE), (window_root(), BOX_WINDOW)]
    pairs += [(Path(m['source']), PurePosixPath(m['target'])) for m in definition['mounts']]
    return sorted(pairs, key=lambda pair: len(str(pair[0])), reverse=True)


def run_command(name, definition, image, engine):
    root().mkdir(parents=True, exist_ok=True)
    command = engine.run_prefix(container(name)) + [
        '--platform', PLATFORM,
        '--label', f'computer-artist.box={name}',
        '--hostname', 'ca-' + name.lower().replace('_', '-'),
        '--stop-timeout', '10',
        '--security-opt', 'label=disable',
    ]  # fmt: skip
    command += user_args(engine)
    binds = [(root().resolve(), BOX_STATE, 'rw'), (window_root(), BOX_WINDOW, 'rw')]
    binds += [(Path(m['source']), m['target'], m['mode']) for m in definition['mounts']]
    for source, target, mode in binds:
        option = f'type=bind,source={source},target={target}'
        command += ['--mount', option + (',readonly' if mode == 'ro' else '')]
    if definition['network'] != 'private':
        command.append('--network=' + definition['network'])
    if definition['cpus']:
        command += ['--cpus', str(definition['cpus'])]
    if definition['memory']:
        command += ['--memory', definition['memory']]
    for k, v in definition['env'].items():
        command += ['--env', f'{k}={v}']
    if definition['workdir']:
        command += ['--workdir', definition['workdir']]
    command += [image, name, '--width', str(definition['width'])]
    command += ['--height', str(definition['height'])]
    return command + (['--xwayland'] if definition['xwayland'] else [])


def user_args(engine):
    """Who runs the box so that it can write the host folders it mounts.

    Docker Desktop on Windows and macOS shares host folders with any container
    user, so the image's own user stays. On Linux the box user must map to
    the invoking user.
    """
    if docker_only():
        return []
    if engine.name == 'podman':
        return ['--userns=keep-id:uid=1000,gid=1000']
    if engine.rootless:
        return ['--user', '0:0']  # Rootless container root is the invoking user.
    return ['--user', f'{os.getuid()}:{os.getgid()}']


def running(name, engine):
    return engine.state(container(name), 'Running') == 'true'


def inside(name, engine, argv, timeout=60):
    """Run ca inside the box and return its parsed JSON reply."""
    result = subprocess.run(
        engine.exec_command(container(name), ['ca', *argv]),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    try:
        reply = json.loads(result.stdout)
    except json.JSONDecodeError:
        reply = None
    if not isinstance(reply, dict):
        detail = (result.stderr or result.stdout).strip()[-1500:]
        raise RuntimeError(f'Box command ca {" ".join(argv)} failed: {detail}')
    return translate_value(reply, mapping(stored(name) or DEFAULTS))


# Lifecycle


def start(args):
    name = check_name(args.name)
    changes = bool(getattr(args, 'recipe', None) or flag_values(args))
    definition = stored(name)
    engine = engine_for(definition)
    if running(name, engine):
        if changes:
            raise ValueError(f'Environment {name!r} is running; stop it before changing it')
        return {**status(name), 'already_running': True}
    began = time.monotonic()
    definition = define(name, args) if changes or definition is None else definition
    engine = engine_for(definition)
    engine.check()
    (folder(name) / 'logs').mkdir(parents=True, exist_ok=True)
    log = folder(name) / 'logs' / 'build.log'
    image = ensure_image(name, definition, engine, log, getattr(args, 'pull', False))
    if engine.exists(container(name)):
        engine.remove(container(name))
    result = subprocess.run(
        run_command(name, definition, image, engine), capture_output=True, text=True, timeout=120
    )
    if result.returncode:
        raise RuntimeError('Box failed to start: ' + result.stderr.strip()[-2000:])
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if not running(name, engine):
            logs = subprocess.run(
                [engine.name, 'logs', '--tail', '30', container(name)],
                capture_output=True,
                text=True,
            )
            raise RuntimeError('Box exited during startup:\n' + (logs.stdout + logs.stderr)[-2000:])
        try:
            info = inside(name, engine, ['env', 'status', name], timeout=15)
            if info.get('running'):
                break
        except (RuntimeError, subprocess.TimeoutExpired):
            pass
        time.sleep(0.5)
    else:
        stop(name)
        raise RuntimeError(f'Box desktop did not become ready; see ca env logs {name} kwin')
    launched = []
    if not getattr(args, 'no_apps', False):
        try:
            for app in definition['apps']:
                launched.append(launch(name, app))
                if not launched[-1].get('ok'):
                    raise RuntimeError(f'Startup app {app["command"]} failed: {launched[-1]}')
        except BaseException:
            stop(name)
            raise
    return {
        **status(name),
        'startup_seconds': round(time.monotonic() - began, 2),
        'launched': launched,
        'next': f'ca --environment {name} windows',
    }


def launch(name, app):
    argv = ['env', 'exec', name]
    if app.get('cwd'):
        argv += ['--cwd', app['cwd']]
    for k, v in (app.get('env') or {}).items():
        argv += ['--env', f'{k}={v}']
    if app.get('name'):
        argv += ['--name', app['name']]
    elif app.get('wait_window') is not None:
        argv += ['--wait-window', app['wait_window']]
    argv += ['--timeout', str(app.get('timeout', 60)), '--', *app['command']]
    return inside(name, engine_for(stored(name)), argv, timeout=app.get('timeout', 60) + 30)


def stop(name):
    definition = stored(name)
    engine = engine_for(definition)
    was = running(name, engine)
    if engine.exists(container(name)):
        subprocess.run(
            [engine.name, 'stop', '--time', '10', container(name)], capture_output=True, timeout=60
        )
        engine.remove(container(name))
    if engine.exists(container(name)):
        raise RuntimeError(f'Stop incomplete; container {container(name)} is still present')
    return {
        'ok': True,
        'name': name,
        'running': False,
        'stopped': was,
        'evidence': str(folder(name) / 'output'),
        'logs': str(folder(name) / 'logs'),
    }


def status(name):
    definition = stored(name)
    try:
        engine = engine_for(definition)
    except RuntimeError as error:
        return {'ok': True, 'name': name, 'running': False, 'defined': definition is not None,
                'backend': 'linux_box', 'error': str(error)}  # fmt: skip
    info = {
        'ok': True,
        'name': name,
        'running': running(name, engine),
        'defined': definition is not None,
        'backend': 'linux_box',
        'container': container(name),
        'container_runtime': engine.name,
        'state': str(folder(name)),
    }
    if info['running']:
        try:
            inner = inside(name, engine, ['env', 'status', name], timeout=15)
            info.update({k: v for k, v in inner.items() if k not in info or k == 'running'})
        except (RuntimeError, subprocess.TimeoutExpired) as error:
            info.update(running=None, error=str(error))
    if definition:
        info['paths'] = {str(b): str(h) for h, b in mapping(definition)}
    return info


def remove(name, confirmed):
    if not folder(name).exists():
        raise ValueError(f'Environment {name!r} does not exist')
    definition = stored(name)
    engine = engine_for(definition)
    if running(name, engine):
        raise ValueError(f'Environment {name!r} is running; stop it first')
    if not confirmed:
        raise ValueError(
            f'Removing deletes {folder(name)} including window maps and evidence; pass --yes'
        )
    import shutil

    shutil.rmtree(folder(name))
    engine.remove_image(f'localhost/computer-artist-box-{name.lower()}:latest')
    return {'ok': True, 'name': name, 'removed': str(folder(name))}


def doctor(name):
    checks = []

    def check(label, passed, detail, remedy=None):
        checks.append(
            {'name': label, 'passed': bool(passed), 'detail': detail}
            | ({'remedy': remedy} if remedy and not passed else {})
        )

    definition = stored(name)
    check('defined', definition, str(folder(name) / 'box.json'), f'ca env create {name}')
    try:
        engine = engine_for(definition)
        engine.check()
        check('container_runtime', True, engine.name)
    except (RuntimeError, ValueError) as error:
        check(
            'container_runtime',
            False,
            str(error),
            'Install Docker Desktop' + ('' if docker_only() else ' or Podman'),
        )
        engine = None
    try:
        source = checkout()
        tag = base_tag(source)
        present = engine is not None and engine.image_exists(tag)
        check('box_image', True, tag + ('' if present else ' (built on first start)'))
    except ValueError as error:
        check('box_image', False, str(error))
    report = {'ok': True, 'name': name, 'backend': 'linux_box'}
    if engine is not None and running(name, engine):
        try:
            report['inside'] = inside(name, engine, ['env', 'status', name], timeout=15)
            check('desktop', report['inside'].get('running'), 'private KWin inside the box')
        except (RuntimeError, subprocess.TimeoutExpired) as error:
            check('desktop', False, str(error), f'ca env stop {name} && ca env start {name}')
    report.update(ok=all(c['passed'] for c in checks), checks=checks)
    return report


# Running ca inside a box


def to_box(value, pairs, cwd=None):
    path = Path(value)
    if not path.is_absolute():
        path = Path(cwd or os.getcwd()) / path
    path = Path(os.path.abspath(path))
    for host, box in pairs:
        if path == host or host in path.parents:
            return str(box.joinpath(*path.relative_to(host).parts))
    raise ValueError(
        f'{value} is not shared with the environment; use the project folder, a mount or '
        'the environment output folder (ca env status lists the shared paths)'
    )


def to_host(text, pairs):
    if not text.startswith('/'):
        return text
    path = PurePosixPath(text)
    for host, box in pairs:
        if path == box or box in path.parents:
            return str(host.joinpath(*path.relative_to(box).parts))
    return text


def translate_value(value, pairs):
    if isinstance(value, str):
        return to_host(value, pairs)
    if isinstance(value, list):
        return [translate_value(v, pairs) for v in value]
    if isinstance(value, dict):
        return {k: translate_value(v, pairs) for k, v in value.items()}
    return value


STRING = re.compile(r'"(?:[^"\\]|\\.)*"')


def translate_line(line, pairs):
    """Rewrite JSON string literals holding box paths; other text passes unchanged."""

    def replace(match):
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return match.group(0)
        mapped = to_host(value, pairs)
        return match.group(0) if mapped == value else json.dumps(mapped)

    return STRING.sub(replace, line)


def translate_args(raw, args, pairs):
    """Replace the host paths argparse found in `raw` with their box paths."""
    result = list(raw)
    for dest in PATH_DESTS:
        value = getattr(args, dest, None)
        if not isinstance(value, (str, Path)) or (dest == 'path' and args.command != 'files'):
            continue
        value = str(value)
        mapped = to_box(value, pairs)
        for index, token in enumerate(result):
            if token == value:
                result[index] = mapped
                break
            if token.startswith('--') and token.endswith('=' + value):
                result[index] = token[: -len(value)] + mapped
                break
    return result


def proxy(name, raw, args):
    """Run one ca command inside the box, streaming translated output; returns its status."""
    definition = stored(name)
    if definition is None:
        raise ValueError(f'Environment {name!r} is not defined; use ca env start {name}')
    engine = engine_for(definition)
    if not running(name, engine):
        raise ValueError(f'Environment {name!r} is not running; use ca env start {name}')
    pairs = mapping(definition)
    argv = translate_args(raw, args, pairs)
    piped = not sys.stdin.isatty() if sys.stdin else False
    command = engine.exec_command(container(name), ['ca', *argv], interactive=piped)
    process = subprocess.Popen(
        command,
        stdin=None if piped else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        text=True,
        encoding='utf-8',
    )
    for line in process.stdout:
        sys.stdout.write(translate_line(line, pairs))
        sys.stdout.flush()
    return process.wait()


def listing():
    names = (
        sorted(
            p.name
            for p in root().iterdir()
            if NAME.fullmatch(p.name) and (p / 'box.json').is_file()
        )
        if root().is_dir()
        else []
    )
    result = []
    for name in names:
        info = status(name)
        result.append({'name': name, 'running': info['running'], 'backend': 'linux_box'})
    return {'ok': True, 'environments': result}


def dispatch(args, raw):
    """`ca env` on a box host; actions that only read or start apps run inside the box."""
    action = args.env_action
    if action == 'list':
        return listing()
    name = check_name(args.name)
    if action == 'create':
        if (
            stored(name)
            and not getattr(args, 'replace', False)
            and not (args.recipe or flag_values(args))
        ):
            raise ValueError(
                f'Environment {name!r} already exists; use flags to update or --replace'
            )
        return {'ok': True, 'name': name, 'definition': define(name, args, replace=args.replace)}
    if action == 'start':
        return start(args)
    if action == 'stop':
        return stop(name)
    if action == 'status':
        return status(name)
    if action == 'show':
        definition = stored(name)
        if definition is None:
            raise ValueError(f'Environment {name!r} has no stored definition')
        return {'ok': True, 'name': name, 'backend': 'linux_box', 'definition': definition}
    if action == 'doctor':
        return doctor(name)
    if action == 'remove':
        return remove(name, args.yes)
    if getattr(args, 'on_host', False):
        raise ValueError('--on-host has no meaning in a Linux box; apps already run inside it')
    return proxy(name, raw, args)
