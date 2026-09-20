"""CLI wiring for Window layouts, API fragments, and the observation/programming API."""
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from .fragments import WindowStore, atomic_json, bind, key
from .runtime import Observations


def add_commands(commands, shared):
    storage = commands.add_parser('storage', parents=[shared], help='Inspect or prune managed temporary runs')
    storage.add_argument('action', choices=('status','clean','keep','unkeep'))
    storage.add_argument('id', nargs='?')
    storage.add_argument('--dry-run', action='store_true')
    runs = commands.add_parser('runs', parents=[shared], help='Inspect recorded executions without replaying input')
    runs.add_argument('action', choices=('list','show'))
    runs.add_argument('id', nargs='?')
    runs.add_argument('--window')
    runs.add_argument('--limit', type=int, default=20)
    observe = commands.add_parser('observe', parents=[shared], help='Capture a window, its state and changes')
    observe.add_argument('--window', required=True)
    observe.add_argument('--since', help='Previous observation ID')
    observe.add_argument('--region', nargs=4, type=float, metavar=('X','Y','WIDTH','HEIGHT'))
    target = commands.add_parser('target', parents=[shared], help='Name a visually guarded region from an observation')
    target.add_argument('window')
    target.add_argument('name')
    target.add_argument('--observation', required=True)
    target.add_argument('--rect', nargs=4, type=float, required=True)
    execute = commands.add_parser('execute', parents=[shared], help='Run stdin Python defining run(ctx), with a hard deadline')
    execute.add_argument('--window', required=True)
    store = commands.add_parser('fragments', parents=[shared], help='Small reusable Python modules')
    actions = store.add_subparsers(dest='fragment_action', required=True)
    for action in ('list', 'show', 'inspect', 'create', 'update', 'run', 'remove', 'history'):
        cmd = actions.add_parser(action, parents=[shared])
        if action != 'list':
            cmd.add_argument('name')
        if action in ('create', 'update'):
            cmd.add_argument('--description', default='')
        if action in ('show', 'inspect', 'run'):
            cmd.add_argument('--version')
        if action == 'run':
            cmd.add_argument('--window', required=True, help='Live window to execute against')
            cmd.add_argument('--args', default='{}', help='JSON parameters; named --parameter value also accepted')


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
            if value not in ('true','false'):
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
    store = WindowStore(args.window_dir, args.output_dir)
    if args.command == 'storage':
        from .storage import inspect, cleanup, preserve
        root = store.output
        if args.action in ('keep','unkeep'):
            if not args.id: raise ValueError('keep/unkeep requires a run ID')
            preserve(root,args.id,args.action=='keep')
            return {'ok':True,'id':args.id,'preserved':args.action=='keep'}
        return {'ok':True, **(inspect(root) if args.action=='status' else cleanup(root,dry_run=args.dry_run))}
    if args.command == 'runs':
        if args.action == 'show':
            if not args.id: raise ValueError('runs show requires a run ID')
            return {'ok': True, 'result': json.loads((store.output/key(args.id)/'result.json').read_text())}
        if args.limit < 1 or args.limit > 1000: raise ValueError('limit must be between 1 and 1000')
        entries = []
        for path in sorted((store.output).glob('*/result.json'), key=lambda p:p.stat().st_mtime, reverse=True):
            if not (path.parent/'request.json').exists(): continue
            request = json.loads((path.parent/'request.json').read_text())
            if args.window and request['window'] != args.window.strip('{}'): continue
            record = json.loads(path.read_text())
            entries.append({'id':path.parent.name,'window':request['window'],'module':request.get('module'),
                            'version':request.get('version'),'status':record['status'],'duration':record.get('duration')})
            if len(entries) >= args.limit: break
        return {'ok': True, 'result':entries}
    if args.command == 'target':
        return {'ok': True, 'target': Observations(store, None).target(args.window,args.name,args.observation,args.rect)}
    if args.command != 'fragments' or args.fragment_action == 'run':
        return None
    action = args.fragment_action
    if action == 'list':
        result = store.list()
    elif action in ('show','inspect'):
        info, source = store.load(args.name,args.version)
        result = {'manifest': info, 'source': source}
    elif action in ('create','update'):
        result = store.write(args.name,sys.stdin.read(),description=args.description,update=action=='update')
    elif action == 'history':
        result = store.history(args.name)
    elif action == 'remove':
        result = store.remove(args.name)
    return {'ok': True, 'result': result}


def run_worker(args, extra, socket_path):
    store = WindowStore(args.window_dir, args.output_dir)
    spec = {'socket': socket_path, 'window': args.window.strip('{}'), 'window_root': str(store.root), 'output_root': str(store.output),
            'deadline': args.deadline, 'budget': args.budget, 'lane': args.lane}
    if args.budget <= 0:
        raise ValueError('budget must be positive')
    if args.command == 'fragments':
        info, _ = store.load(args.name,args.version)
        spec.update(module=args.name,version=info['version'],arguments=arguments(info,args.args,extra))
    else:
        from .fragments import contract
        source = sys.stdin.read()
        info = contract(source)
        bind(info,{})
        spec.update(source=source,arguments={})
    from .storage import managed_run
    with managed_run(store.output) as folder:
        return _supervise(args, spec, folder.name, folder)


def _supervise(args, spec, run_id, folder):
    atomic_json(folder/'request.json', spec)
    started = time.monotonic()
    process = None
    failure = None
    with (folder/'.active.lock').open('r') as lease, (folder/'program.log').open('w') as log:
        fcntl.flock(lease.fileno(), fcntl.LOCK_SH)
        try:
            process = subprocess.Popen([sys.executable,'-m','computer_artist.worker',str(folder)],
                                       cwd=Path(__file__).resolve().parents[1], stdout=log,stderr=subprocess.STDOUT,start_new_session=True,pass_fds=(lease.fileno(),))
            process.wait(timeout=args.deadline)
        except subprocess.TimeoutExpired:
            failure = 'Hard execution deadline exceeded'
        except KeyboardInterrupt:
            failure = 'Execution interrupted'
        finally:
            if process:
                # Kill any remaining descendants as well as a stalled worker.
                try: os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError: pass
                try: process.wait(timeout=.5)
                except subprocess.TimeoutExpired: pass
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                process.wait()
    if failure:
        result = {'ok': False, 'status': 'interrupted', 'error': failure}
    elif (folder/'result.json').exists():
        result = json.loads((folder/'result.json').read_text())
    else:
        result = {'ok': False, 'status': 'failed', 'error': f'Worker exited without a result ({process.returncode})'}
    result.update(run_id=run_id, duration=round(time.monotonic()-started,3), log=str(folder/'program.log'), record=str(folder/'result.json'))
    atomic_json(folder/'trace.json', result.get('trace', []))
    atomic_json(folder/'result.json',result)
    if args.trace:
        atomic_json(args.trace, result)
    # Keep detailed events and traces on disk; default tool feedback stays compact.
    summary = {k:v for k,v in result.items() if k not in ('trace','modules')}
    if 'modules' in result:
        summary['modules'] = [{k:entry.get(k) for k in ('module','version','lane','status')} for entry in result['modules']]
    return summary


def connected_command(client, args):
    if args.command == 'observe':
        return {'ok': True, 'observation': Observations(WindowStore(args.window_dir, args.output_dir),client).capture(args.window.strip('{}'),since=args.since,region=args.region)}
    return None
