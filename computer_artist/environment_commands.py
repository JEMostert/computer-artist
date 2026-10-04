"""The ca env command group."""

import argparse
from pathlib import Path

from .environment_apps import browser, launch, launch_apps, logs
from .environments import (
    DEFAULTS,
    ENV_KEY,
    NAME,
    check_name,
    define,
    doctor,
    flag_values,
    kind,
    remove,
    start,
    state_root,
    status,
    stop,
    stored,
)


def add_definition_arguments(parser):
    group = parser.add_argument_group('definition (stored; flags update it)')
    group.add_argument(
        '--recipe', type=Path, help='TOML/JSON recipe; relative paths follow the file'
    )
    source = group.add_mutually_exclusive_group()
    source.add_argument(
        '--image', help='Run applications in a rootless Podman container from IMAGE'
    )
    source.add_argument('--containerfile', type=Path, help='Build the application image (cached)')
    group.add_argument('--context', type=Path, help='Build context (default: Containerfile folder)')
    group.add_argument(
        '--project', type=Path, help='Mount read-write at the same path; default working directory'
    )
    group.add_argument(
        '--mount',
        action='append',
        metavar='SOURCE[:TARGET][:ro|rw]',
        help='Extra folder; repeatable',
    )
    group.add_argument('--workdir', help='Container working directory')
    group.add_argument(
        '--env',
        action='append',
        metavar='KEY=VALUE',
        type=env_item,
        help='Application environment; repeatable',
    )
    group.add_argument('--cpus', type=float)
    group.add_argument('--memory', help='Container memory limit, e.g. 4g')
    group.add_argument(
        '--gpu',
        action=argparse.BooleanOptionalAction,
        default=None,
        help='Pass /dev/dri (and NVIDIA CDI when configured)',
    )
    group.add_argument('--network', choices=('private', 'host', 'none'))
    group.add_argument(
        '--xwayland',
        action=argparse.BooleanOptionalAction,
        default=None,
        help='Private XWayland; input compatibility is reported separately',
    )
    group.add_argument('--width', type=int)
    group.add_argument('--height', type=int)


def env_item(value):
    if '=' not in value or not ENV_KEY.fullmatch(value.split('=', 1)[0]):
        raise argparse.ArgumentTypeError('expected KEY=VALUE')
    return value


def add_command(commands):
    parser = commands.add_parser(
        'env',
        help='Private agent desktops (rootless Podman or host apps) that never use your desktop',
        description='Create, start and use private agent desktops. Select one in other commands '
        'with ca --environment NAME ...; a stopped or missing environment is an error, never a '
        'fallback to your desktop.',
    )
    actions = parser.add_subparsers(dest='env_action', required=True)
    actions.add_parser('list', help='Defined environments and whether they run')
    create = actions.add_parser('create', help='Store a definition without starting it')
    create.add_argument('name')
    create.add_argument('--replace', action='store_true', help='Discard the previous definition')
    add_definition_arguments(create)
    start_parser = actions.add_parser(
        'start', help='Start (idempotent); flags update the definition first'
    )
    start_parser.add_argument('name')
    add_definition_arguments(start_parser)
    start_parser.add_argument('--plugin', help='Plugin library (default: checkout build)')
    start_parser.add_argument('--pull', action='store_true', help='Refresh base images')
    start_parser.add_argument('--no-apps', action='store_true', help='Skip the recipe startup apps')
    for action, text in (
        ('stop', 'Stop the desktop, container and apps; evidence is kept'),
        ('status', 'Running state, container and applications'),
        ('show', 'Stored definition'),
        ('doctor', 'Preflight and runtime checks: tools, image, GPU, services, apps'),
    ):
        actions.add_parser(action, help=text).add_argument('name')
    remove_parser = actions.add_parser('remove', help='Delete a stopped environment and its data')
    remove_parser.add_argument('name')
    remove_parser.add_argument('--yes', action='store_true')
    logs = actions.add_parser('logs', help='Tail kwin, build, podman or an application log')
    logs.add_argument('name')
    logs.add_argument('source', nargs='?', help='kwin, build, podman or an app id (default: list)')
    logs.add_argument('--lines', type=int, default=40)
    execute = actions.add_parser('exec', help='Start an application inside the environment')
    execute.add_argument('name')
    add_launch_arguments(execute)
    execute.add_argument('--cwd', help='Working directory (container path for container apps)')
    execute.add_argument('--env', action='append', type=env_item, metavar='KEY=VALUE')
    execute.add_argument(
        '--wait', action='store_true', help='Wait for the command to exit; report status and output'
    )
    execute.add_argument('argv', nargs='*', help='Command after --')
    web = actions.add_parser('browser', help='Open a browser with a private persistent profile')
    web.add_argument('name')
    add_launch_arguments(web)
    web.add_argument('--family', choices=('chromium', 'firefox'), required=True)
    web.add_argument('--executable', required=True, help='e.g. chromium, vivaldi, firefox')
    web.add_argument('--url', default='about:blank')
    web.add_argument('--profile', help='Profile name inside the environment (default: executable)')
    web.add_argument(
        '--copy-profile',
        metavar='PATH|default',
        help='Seed a new profile from a closed browser profile',
    )
    web.add_argument('--replace-profile', action='store_true')
    web.add_argument('browser_args', nargs='*', help='Extra browser arguments after --')


def add_launch_arguments(parser):
    parser.add_argument(
        '--on-host', action='store_true', help='Run a host program instead of a container program'
    )
    parser.add_argument(
        '--wait-window',
        nargs='?',
        const='',
        metavar='TITLE',
        help='Wait for a new window (optionally containing TITLE)',
    )
    parser.add_argument(
        '--window-name',
        '--name',
        dest='window_name',
        help='Name the new window (implies --wait-window)',
    )
    parser.add_argument('--timeout', type=float, default=60)


def dispatch(args):
    action = args.env_action
    if action == 'list':
        root = state_root()
        names = sorted(
            p.name for p in root.iterdir() if p.is_dir() and NAME.fullmatch(p.name)
            and ((p / 'definition.json').exists() or (p / 'environment.json').exists())
        ) if root.is_dir() else []  # fmt: skip
        return {'ok': True, 'environments': [summary(n) for n in names]}
    name = check_name(args.name)
    if action == 'create':
        if stored(name) and not args.replace and not (args.recipe or flag_values(args)):
            raise ValueError(
                f'Environment {name!r} already exists; use flags to update or --replace'
            )
        return {'ok': True, 'name': name, 'definition': define(name, args, replace=args.replace)}
    if action == 'start':
        result = start(args)
        if result.get('already_running'):
            return result
        try:
            definition = stored(name)
            launched = [] if args.no_apps else launch_apps(name, definition)
        except BaseException:
            stop(name)
            raise
        return {**result, 'launched': launched, 'next': f'ca --environment {name} windows'}
    if action == 'stop':
        return stop(name)
    if action == 'status':
        return status(name)
    if action == 'show':
        definition = stored(name)
        if definition is None:
            raise ValueError(f'Environment {name!r} has no stored definition')
        return {'ok': True, 'name': name, 'kind': kind(definition), 'definition': definition}
    if action == 'doctor':
        return doctor(name)
    if action == 'remove':
        return remove(name, args.yes)
    if action == 'logs':
        return logs(name, args.source, args.lines)
    if action == 'browser':
        return browser(args)
    command = args.argv[1:] if args.argv[:1] == ['--'] else args.argv
    if not command:
        raise ValueError('Use ca env exec NAME [options] -- application [arguments]')
    return launch(
        name,
        command,
        on_host=args.on_host,
        cwd=args.cwd or default_cwd(name, args.on_host),
        env=dict(item.split('=', 1) for item in args.env or ()),
        wait_window=args.wait_window,
        window_name=args.window_name,
        timeout=args.timeout,
        wait_exit=args.wait,
    )


def default_cwd(name, on_host):
    """Host apps start here; container apps here only when mounted at the same path."""
    here = Path.cwd()
    definition = stored(name) or DEFAULTS
    if on_host or kind(definition) == 'host':
        return str(here)
    for mount in definition['mounts']:
        if mount['source'] == mount['target'] and here.is_relative_to(mount['target']):
            return str(here)
    return None


def summary(name):
    info = status(name)
    definition = stored(name) or {}
    return {
        'name': name,
        'running': info['running'],
        'kind': kind(definition) if definition else info.get('kind'),
        'image': definition.get('image'),
        'containerfile': definition.get('containerfile'),
        'apps': len(info.get('applications', [])),
    }
