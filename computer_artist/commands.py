"""CLI commands for workspace data, observations and supervised programs."""

import json
import os
import stat
import sys

from .contracts import bind, contract
from .observations import Observations
from .supervisor import run_supervised
from .workspace import Workspace


def add_commands(commands, shared):
    storage = commands.add_parser(
        'storage', parents=[shared], help='Inspect or prune managed temporary runs'
    )
    storage.add_argument('action', choices=('status', 'clean', 'keep', 'unkeep'))
    storage.add_argument('id', nargs='?')
    storage.add_argument('--dry-run', action='store_true')
    runs = commands.add_parser(
        'runs', parents=[shared], help='Inspect recorded executions without replaying input'
    )
    runs.add_argument('action', choices=('list', 'show', 'inspect', 'stop'))
    runs.add_argument('id', nargs='?')
    runs.add_argument('--window')
    runs.add_argument('--limit', type=int, default=20)
    files = commands.add_parser(
        'files', parents=[shared], help='Inspect exported files without desktop input'
    )
    files.add_argument('action', choices=('inspect', 'snapshot'))
    files.add_argument('path')
    files.add_argument('--kind', choices=('file', 'text', 'json', 'image'), default='file')
    observe = commands.add_parser(
        'observe', parents=[shared], help='Capture a window, its state and changes'
    )
    observe.add_argument('--window', required=True)
    observe.add_argument('--since', help='Previous observation ID')
    observe.add_argument('--region', nargs=4, type=float, metavar=('X', 'Y', 'WIDTH', 'HEIGHT'))
    target = commands.add_parser(
        'target', parents=[shared], help='Name a visually guarded region from an observation'
    )
    target.add_argument('window')
    target.add_argument('name')
    target.add_argument('--observation', required=True)
    guard = target.add_mutually_exclusive_group(required=True)
    guard.add_argument('--rect', nargs=4, type=float)
    guard.add_argument(
        '--revalidate',
        action='store_true',
        help='Explicitly adopt an existing unchanged guard for this live window',
    )
    execute = commands.add_parser(
        'execute', parents=[shared], help='Run stdin Python defining run(ctx), with a hard deadline'
    )
    execute.add_argument('--window', required=True)
    store = commands.add_parser('fragments', parents=[shared], help='Small reusable Python modules')
    actions = store.add_subparsers(dest='fragment_action', required=True)
    for action in (
        'list',
        'show',
        'inspect',
        'create',
        'update',
        'run',
        'remove',
        'history',
        'restore',
    ):
        cmd = actions.add_parser(action, parents=[shared])
        if action != 'list':
            cmd.add_argument('name')
        if action in ('create', 'update'):
            cmd.add_argument('--description', default='')
        if action in ('show', 'inspect', 'run', 'restore'):
            cmd.add_argument('--version', required=action == 'restore')
        if action == 'run':
            cmd.add_argument('--window', required=True, help='Live window to execute against')
            cmd.add_argument(
                '--args',
                default='{}',
                help='JSON parameters; named --parameter value also accepted',
            )


def arguments(manifest, encoded, extra):
    values = json.loads(encoded)
    if not isinstance(values, dict):
        raise ValueError('--args must be a JSON object')
    while extra:
        option, *extra = extra
        if not option.startswith('--'):
            raise ValueError('Module parameters use --name value')
        name = option[2:].replace('-', '_')
        if name not in manifest['parameters']:
            raise ValueError(f'Unknown module parameter: {name}')
        if not extra:
            raise ValueError(f'Missing value for {option}')
        value, *extra = extra
        if name in values:
            raise ValueError(f'Duplicate parameter: {name}')
        kind = manifest['parameters'][name]['type']
        if kind == 'bool':
            if value not in ('true', 'false'):
                raise ValueError(f'{name} must be true or false')
            value = value == 'true'
        elif kind == 'int':
            value = int(value)
        elif kind == 'float':
            value = float(value)
        values[name] = value
    return bind(manifest, values)


def local_command(args, extra):
    """Return None for commands requiring a desktop connection."""
    store = Workspace(args.window_dir, args.output_dir)
    if args.command == 'storage':
        from .storage import cleanup, inspect, preserve

        root = store.output
        if args.action in ('keep', 'unkeep'):
            if not args.id:
                raise ValueError('keep/unkeep requires a run ID')
            preserve(root, args.id, args.action == 'keep')
            return {'ok': True, 'id': args.id, 'preserved': args.action == 'keep'}
        return {
            'ok': True,
            **(inspect(root) if args.action == 'status' else cleanup(root, dry_run=args.dry_run)),
        }
    if args.command == 'files':
        from .artifacts import inspect_file, snapshot_file

        result = (
            snapshot_file(args.path)
            if args.action == 'snapshot'
            else inspect_file(args.path, kind=args.kind)
        )
        return {'ok': True, 'result': result}
    if args.command == 'runs':
        from .records import inspect_run, list_runs

        if args.action == 'list':
            if args.id:
                raise ValueError('runs list takes --window/--limit, not an ID')
            result = list_runs(store.output, args.window, store, limit=args.limit)
            return {'ok': True, 'result': result['runs'], 'issues': result['issues']}
        if not args.id:
            raise ValueError(f'runs {args.action} requires a run ID')
        if args.window:
            raise ValueError('--window only filters runs list')
        if args.action == 'stop':
            from .control import request_stop

            return {'ok': True, **request_stop(store.output, args.id)}
        dossier = inspect_run(store.output, args.id)
        return {'ok': True, **({'result': dossier['result']} if args.action == 'show' else dossier)}
    if args.command != 'fragments' or args.fragment_action == 'run':
        return None
    action = args.fragment_action
    if action == 'list':
        result = store.fragments.list()
    elif action in ('show', 'inspect'):
        info, source = store.fragments.load(args.name, args.version)
        result = {'manifest': info, 'source': source}
    elif action in ('create', 'update'):
        result = store.fragments.write(
            args.name, sys.stdin.read(), description=args.description, update=action == 'update'
        )
    elif action == 'history':
        result = store.fragments.history(args.name)
    elif action == 'remove':
        result = store.fragments.remove(args.name)
    elif action == 'restore':
        result = store.fragments.restore(args.name, args.version)
    return {'ok': True, 'result': result}


def run_program(args, extra, socket_path):
    store = Workspace(args.window_dir, args.output_dir)
    spec = {
        'socket': socket_path,
        'window': store.resolve_window(args.window.strip('{}')),
        'window_root': str(store.root),
        'output_root': str(store.output),
        'deadline': args.deadline,
        'budget': args.budget,
        'lane': args.lane,
    }
    if args.budget <= 0:
        raise ValueError('budget must be positive')
    if args.command == 'draw':
        from .gestures import svg_path

        if args.path_file:
            fd = os.open(args.path_file, os.O_RDONLY | os.O_NONBLOCK)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise ValueError('Path file must be a regular UTF-8 text file')
                with os.fdopen(fd, 'rb', closefd=False) as stream:
                    encoded = stream.read(1048577)
                if len(encoded) > 1048576:
                    raise ValueError('Path file exceeds 1 MiB')
                data = encoded.decode('utf-8')
            finally:
                os.close(fd)
        else:
            data = args.path
        svg_path(data, origin=args.origin, scale=args.scale, spacing=args.spacing)
        source = (
            'def run(ctx):\n'
            + f'    result = ctx.svg_path({data!r}, origin={tuple(args.origin)!r}, scale={args.scale!r}, spacing={args.spacing!r}, interval={args.interval!r}, verify_change={args.verify_change!r})\n'
            + '    return result\n'
        )
        spec.update(source=source, arguments={}, kind='draw')
    elif args.command == 'fragments':
        info, _ = store.fragments.load(args.name, args.version)
        spec.update(
            module=args.name, version=info['version'], arguments=arguments(info, args.args, extra)
        )
    else:
        source = sys.stdin.read()
        info = contract(source)
        bind(info, {})
        spec.update(source=source, arguments={})
    return run_supervised(spec, trace=args.trace)


def connected_command(client, args):
    if args.command == 'target':
        observer = Observations(Workspace(args.window_dir, args.output_dir), client)
        result = (
            observer.revalidate(args.window, args.name, args.observation)
            if args.revalidate
            else observer.target(args.window, args.name, args.observation, args.rect)
        )
        return {'ok': True, 'target': result}
    if args.command == 'observe':
        return {
            'ok': True,
            'observation': Observations(
                Workspace(args.window_dir, args.output_dir), client
            ).capture(args.window.strip('{}'), since=args.since, region=args.region),
        }
    return None
