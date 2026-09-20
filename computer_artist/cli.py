"""Terminal interface to the independent compositor seat."""
import argparse
from contextlib import redirect_stdout
import json
import math
import os
from pathlib import Path
import runpy
import signal
import sys

from .client import Client, PLAIN

BUTTONS = {'left': 272, 'right': 273, 'middle': 274}
KEYS = {**PLAIN, 'ctrl': 29, 'control': 29, 'shift': 42, 'alt': 56,
        'super': 125, 'meta': 125, 'enter': 28, 'return': 28, 'tab': 15,
        'escape': 1, 'esc': 1, 'backspace': 14, 'delete': 111, 'space': 57,
        'left': 105, 'right': 106, 'up': 103, 'down': 108,
        'home': 102, 'end': 107, 'pageup': 104, 'pagedown': 109,
        **{f'f{i}': 58+i for i in range(1, 11)}, 'f11': 87, 'f12': 88}


def finite_number(value):
    number = float(value)
    if not math.isfinite(number):
        raise argparse.ArgumentTypeError('must be a finite number')
    return number


def positive_number(value):
    number = finite_number(value)
    if number <= 0:
        raise argparse.ArgumentTypeError('must be greater than zero')
    return number


def chord(value):
    try:
        codes = [KEYS[name.lower()] for name in value.split('+')]
    except KeyError as error:
        raise argparse.ArgumentTypeError(f'unknown key: {error.args[0]}') from None
    if len(codes) != len(set(codes)):
        raise argparse.ArgumentTypeError('a chord cannot hold the same key twice')
    return codes


def parser():
    shared = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    shared.add_argument('--socket', help='Control socket (default: CA_SOCKET, then the host session)')
    shared.add_argument('--deadline', type=positive_number, help='Action deadline in seconds (default: 120)')
    shared.add_argument('--trace', help='Write an action trace as JSON')
    lanes = shared.add_mutually_exclusive_group()
    lanes.add_argument('--host', dest='lane', action='store_const', const='host', help='Explicitly control the real desktop pointer/focus')
    lanes.add_argument('--agent', dest='lane', action='store_const', const='agent', help='Control the independent agent pointer (default)')
    result = argparse.ArgumentParser(prog='ca', parents=[shared], description='Computer Artist: independent agent input on your desktop')
    # Shared options are declared before child parsers copy them.
    shared.add_argument('--window-dir', help='Window layouts and API fragments root')
    shared.add_argument('--output-dir', help='Run output root')
    result.add_argument('--output-dir', default=argparse.SUPPRESS)
    shared.add_argument('--budget', type=int, help='Execution request budget (default: 20000)')
    result.add_argument('--window-dir', default=argparse.SUPPRESS)
    result.add_argument('--budget', type=int, default=argparse.SUPPRESS)
    commands = result.add_subparsers(dest='command', required=True)
    from .setup import add_command
    add_command(commands)
    for name, help_text in [('windows', 'List windows and their IDs'),
                            ('capabilities', 'Report compositor support'),
                            ('stop', 'Revoke the current agent lease and return its app'),
                            ('takeover', 'Alias for stop')]:
        commands.add_parser(name, parents=[shared], help=help_text)
    session = commands.add_parser('session', parents=[shared], help='Inspect or close the automatically opened cursor session')
    session.add_argument('action', choices=('status', 'close'))
    for name in ('click', 'move', 'type', 'key', 'scroll', 'focus'):
        command = commands.add_parser(name, parents=[shared], help=f'{name.capitalize()} in an explicitly selected window')
        command.add_argument('--window', required=True, help='Exact ID from ca windows')
        if name in ('click', 'move', 'scroll'):
            command.add_argument('--x', required=True, type=finite_number, help='Logical pixels from the left of the window content')
            command.add_argument('--y', required=True, type=finite_number, help='Logical pixels from the top of the window content')
            command.add_argument('--absolute', action='store_true', help='Use desktop logical coordinates instead')
        if name == 'click':
            command.add_argument('--button', choices=BUTTONS, default='left')
        elif name == 'type':
            command.add_argument('text', help='Literal US-keyboard text; use -- before text starting with a dash')
        elif name == 'key':
            command.add_argument('chord', type=chord, help='Key or chord, e.g. End or Ctrl+Shift+S')
        elif name == 'scroll':
            command.add_argument('--delta', required=True, type=finite_number, help='Signed scroll delta; positive is down/right')
            command.add_argument('--axis', choices=('vertical', 'horizontal'), default='vertical')
    run = commands.add_parser('run', parents=[shared], help='Run a Python file defining main(client)')
    run.add_argument('program')
    capture = commands.add_parser('capture', parents=[shared], help='Capture a window with a backend that supports capture')
    capture.add_argument('--window', required=True)
    capture.add_argument('output', help='New PNG file; existing files are not overwritten')
    from .harness import add_commands
    add_commands(commands, shared)
    return result


def socket_path(explicit):
    if explicit:
        return explicit
    if os.environ.get('CA_SOCKET'):
        return os.environ['CA_SOCKET']
    runtime = os.environ.get('XDG_RUNTIME_DIR')
    if not runtime:
        raise ValueError('Set CA_SOCKET or pass --socket; XDG_RUNTIME_DIR is unavailable')
    return str(Path(runtime) / 'computer-artist/control')


def find_window(client, identity):
    window = next((w for w in client.windows() if w['id'] == identity), None)
    if window is None:
        raise ValueError(f'Window {identity!r} was not found; run ca windows')
    if not window.get('native'):
        raise ValueError('XWayland input handoff is not supported')
    if not window.get('visible'):
        raise ValueError('The target window is not visible on the current desktop')
    return window


def execute(client, args):
    from .harness import connected_command
    reply = connected_command(client, args)
    if reply is not None:
        return reply
    if args.command == 'session':
        return client.request('session_' + args.action)
    if args.command in ('windows', 'capabilities', 'stop', 'takeover'):
        reply = client.request('takeover' if args.command in ('stop', 'takeover') else args.command)
        if args.command == 'capabilities':
            reply['harness'] = {'fragments': True, 'typed_parameters': ['int','float','str','bool'],
                'observations': True, 'image_diffs': True, 'guarded_regions': True,
                'conditional_programs': True, 'hard_deadlines': True, 'version_history': True,
                'accessibility': False, 'automatic_control_detection': False}
        return reply
    if args.command == 'capture':
        if 'capture' not in client.request('capabilities').get('operations', []):
            raise ValueError('This backend does not support window capture')
        return client.request('capture', window=args.window.strip('{}'), path=str(Path(args.output).resolve()))
    if args.command == 'run':
        with redirect_stdout(sys.stderr):
            program = runpy.run_path(args.program)
            if not callable(program.get('main')):
                raise ValueError('Program must define main(client)')
            program['main'](client)
        client.release()
        return {'ok': True, 'command': 'run', 'status': 'program_returned', 'released': True}
    identity = args.window.strip('{}')
    find_window(client, identity)
    if args.command == 'focus' and client.lane != 'host':
        raise ValueError('Desktop focus changes require explicit --host')
    if args.command in ('type', 'key'):
        operations = client.request('capabilities').get('operations', [])
        if 'key' not in operations or 'focus' not in operations:
            raise ValueError('This lane does not support keyboard input; check ca capabilities')
    with client.owned(identity):
        window = find_window(client, identity)
        if args.command in ('click', 'move', 'scroll'):
            x = args.x if args.absolute else window['x'] + args.x
            y = args.y if args.absolute else window['y'] + args.y
            if not (window['x'] <= x < window['x'] + window['width'] and window['y'] <= y < window['y'] + window['height']):
                raise ValueError('Coordinates lie outside the selected window content')
            if args.command == 'click':
                client.click(x, y, BUTTONS[args.button])
            else:
                client.move(x, y)
                if args.command == 'scroll':
                    client.scroll(args.delta, axis=args.axis)
        elif args.command == 'focus':
            client.focus(identity)
        else:
            # Preserve the app's selected field/caret; no invented click location.
            client.focus(identity)
            if args.command == 'type':
                client.type_text(args.text)
            else:
                client.chord(*args.chord)
    return {'ok': True, 'command': args.command, 'window': identity,
            'status': 'dispatched', 'released': True}


def main(argv=None):
    argument_parser = parser()
    args, extra = argument_parser.parse_known_args(argv)
    if extra and not (args.command == 'fragments' and args.fragment_action == 'run'):
        argument_parser.error('unrecognized arguments: ' + ' '.join(extra))
    for name, default in (('socket', None), ('deadline', 120), ('trace', None), ('window_dir', None), ('output_dir', None), ('budget', 20000), ('lane', 'agent')):
        vars(args).setdefault(name, default)
    client = None
    previous = signal.getsignal(signal.SIGTERM)

    def terminate(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    try:
        if args.command == 'setup':
            from .setup import install
            print(json.dumps(install(args), indent=2))
            return 0
        from .harness import local_command, run_worker
        reply = local_command(args, extra)
        if reply is not None:
            print(json.dumps(reply, indent=2))
            return 0
        if args.command == 'execute' or (args.command == 'fragments' and args.fragment_action == 'run'):
            reply = run_worker(args, extra, socket_path(args.socket))
            print(json.dumps(reply, indent=2))
            return 0 if reply['ok'] else 1
        if args.command == 'type':
            Client.text_sequence(args.text)  # Reject unsupported text before acquiring anything.
        path = socket_path(args.socket)
        try:
            client = Client(path, deadline=args.deadline, lane=args.lane)
        except OSError as error:
            raise ConnectionError(f'Cannot connect to the agent compositor at {path}: {error.strerror}. Set CA_SOCKET or use --socket for your test session.') from None
        try:
            with client:
                reply = execute(client, args)
        finally:
            if args.trace:
                client.save_trace(args.trace)
        print(json.dumps(reply, indent=2))
        return 0
    except KeyboardInterrupt:
        print(json.dumps({'ok': False, 'error': 'interrupted'}), file=sys.stderr)
        return 130
    except Exception as error:
        print(json.dumps({'ok': False, 'error': str(error)}), file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous)
