"""Terminal interface to the independent compositor seat."""

import argparse
import json
import math
import signal
import sys
from pathlib import Path

from .client import Client
from .config import socket_path
from .input import BUTTONS, parse_chord
from .workspace import Workspace


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


def nonnegative_number(value):
    number = finite_number(value)
    if number < 0:
        raise argparse.ArgumentTypeError('must be nonnegative')
    return number


def chord(value):
    try:
        parse_chord(value)
        return value
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None


def parser():
    shared = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    shared.add_argument(
        '--socket', help='Control socket (default: CA_SOCKET, then the host session)'
    )
    shared.add_argument(
        '--deadline', type=positive_number, help='Action deadline in seconds (default: 120)'
    )
    shared.add_argument('--trace', help='Write an action trace as JSON')
    lanes = shared.add_mutually_exclusive_group()
    lanes.add_argument(
        '--host',
        dest='lane',
        action='store_const',
        const='host',
        help='Explicitly use desktop pointer, focus, keyboard and clipboard',
    )
    lanes.add_argument(
        '--agent',
        dest='lane',
        action='store_const',
        const='agent',
        help='Control the independent agent pointer and supported physical keys (default)',
    )
    result = argparse.ArgumentParser(
        prog='ca',
        parents=[shared],
        description='Computer Artist: independent agent input on your desktop',
    )
    result.add_argument('--version', action='version', version='Computer Artist 0.2.0')
    # Shared options are declared before child parsers copy them.
    shared.add_argument('--window-dir', help='Window layouts and fragments root')
    shared.add_argument('--output-dir', help='Run output root')
    result.add_argument('--output-dir', default=argparse.SUPPRESS)
    shared.add_argument('--budget', type=int, help='Execution request budget (default: 20000)')
    result.add_argument('--window-dir', default=argparse.SUPPRESS)
    result.add_argument('--budget', type=int, default=argparse.SUPPRESS)
    commands = result.add_subparsers(dest='command', required=True)
    from .setup import add_command

    add_command(commands)
    doctor = commands.add_parser(
        'doctor',
        parents=[shared],
        help='Read-only diagnosis of installation, backend and workspace',
    )
    doctor.add_argument(
        '--window', help='Also assess a live window ID or saved name for agent input'
    )
    doctor.add_argument(
        '--build', action='store_true', help='Check native plugin build tools and headers'
    )
    watch = commands.add_parser(
        'watch', parents=[shared], help='Stream bounded read-only window observations as JSONL'
    )
    watch.add_argument('--window', required=True)
    watch.add_argument(
        '--duration',
        type=positive_number,
        default=10,
        help='Observe for this many seconds (default: 10)',
    )
    watch.add_argument(
        '--interval',
        type=positive_number,
        default=0.5,
        help='Delay between captures, minimum 0.05 seconds (default: 0.5)',
    )
    watch.add_argument(
        '--region', nargs=4, type=finite_number, metavar=('X', 'Y', 'WIDTH', 'HEIGHT')
    )
    watch.add_argument(
        '--changes-only',
        action='store_true',
        help='Only stream first/changed frames; retain all recent captures',
    )
    draw = commands.add_parser(
        'draw', parents=[shared], help='Draw SVG path geometry through actual pointer input'
    )
    draw.add_argument('--window', required=True)
    source = draw.add_mutually_exclusive_group(required=True)
    source.add_argument(
        '--path', help='SVG path data (the d attribute), e.g. M10 10 C20 0 40 0 50 10'
    )
    source.add_argument(
        '--path-file', type=Path, help='UTF-8 text file containing SVG path data, not XML'
    )
    draw.add_argument('--origin', nargs=2, type=finite_number, default=(0, 0), metavar=('X', 'Y'))
    draw.add_argument('--scale', type=positive_number, default=1)
    draw.add_argument(
        '--spacing',
        type=positive_number,
        default=2,
        help='Maximum sampled step in logical pixels (default: 2)',
    )
    draw.add_argument(
        '--interval',
        type=nonnegative_number,
        default=0.016,
        help='Seconds between sampled points (default: 0.016)',
    )
    draw.add_argument(
        '--verify-change',
        action='store_true',
        help='Require a captured pixel change after drawing; this verifies visible effect, not exact shape',
    )
    for name, help_text in [
        ('windows', 'List windows and their IDs'),
        ('capabilities', 'Report compositor support'),
        ('stop', 'Revoke the current agent lease and return its app'),
        ('takeover', 'Alias for stop'),
    ]:
        commands.add_parser(name, parents=[shared], help=help_text)
    name_window = commands.add_parser(
        'set', parents=[shared], help='Give an open window a short reusable name'
    )
    name_window.add_argument('window', nargs='?', help='Exact live window ID from ca windows')
    name_window.add_argument('--name', required=True, help='Name, such as kolourpaint')
    name_window.add_argument(
        '--title', help='Select one open window whose title contains this text (case insensitive)'
    )
    session = commands.add_parser(
        'session', parents=[shared], help='Inspect or close the automatically opened cursor session'
    )
    session.add_argument('action', choices=('status', 'close'))
    clipboard = commands.add_parser(
        'clipboard', parents=[shared], help='Read or write the shared host text clipboard'
    )
    clipboard.add_argument('action', choices=('get', 'set'))
    clipboard.add_argument('text', nargs='?', help='Text to set; omit to read stdin')
    for name in ('click', 'move', 'type', 'paste', 'key', 'scroll', 'focus'):
        command = commands.add_parser(
            name, parents=[shared], help=f'{name.capitalize()} in an explicitly selected window'
        )
        command.add_argument('--window', required=True, help='Window ID or saved name')
        if name in ('click', 'move', 'scroll'):
            command.add_argument(
                '--x',
                required=True,
                type=finite_number,
                help='Logical pixels from the left of the window content',
            )
            command.add_argument(
                '--y',
                required=True,
                type=finite_number,
                help='Logical pixels from the top of the window content',
            )
            command.add_argument(
                '--absolute', action='store_true', help='Use desktop logical coordinates instead'
            )
        if name == 'click':
            command.add_argument('--button', choices=BUTTONS, default='left')
        elif name in ('type', 'paste'):
            command.add_argument(
                'text', help='Unicode text to paste; use -- before text starting with a dash'
            )
            command.add_argument(
                '--shortcut',
                type=chord,
                default=chord('Shift+Insert'),
                help='Paste shortcut (default: Shift+Insert); use Ctrl+Shift+V for apps that require it',
            )
        elif name == 'key':
            command.add_argument('chord', type=chord, help='Key or chord, e.g. End or Ctrl+Shift+S')
            command.add_argument(
                '--duration',
                type=positive_number,
                default=0,
                help='Hold keys for this many seconds',
            )
        elif name == 'scroll':
            command.add_argument(
                '--delta',
                required=True,
                type=finite_number,
                help='Signed scroll delta; positive is down/right',
            )
            command.add_argument('--axis', choices=('vertical', 'horizontal'), default='vertical')
    capture = commands.add_parser(
        'capture', parents=[shared], help='Capture a window with a backend that supports capture'
    )
    capture.add_argument('--window', required=True, help='Window ID or saved name')
    capture.add_argument('output', help='New PNG file; existing files are not overwritten')
    from .commands import add_commands

    add_commands(commands, shared)
    return result


def execute(client, args):
    from .commands import connected_command

    reply = connected_command(client, args)
    if reply is not None:
        return reply
    if args.command == 'session':
        return client.request('session_' + args.action)
    if args.command in ('windows', 'capabilities', 'stop', 'takeover'):
        reply = client.request('takeover' if args.command in ('stop', 'takeover') else args.command)
        if args.command == 'windows':
            reply['windows'] = client.window_store.display_windows(reply['windows'])
        if args.command == 'capabilities':
            reply['harness'] = {
                'fragments': True,
                'typed_parameters': ['int', 'float', 'str', 'bool'],
                'observations': True,
                'image_diffs': True,
                'guarded_regions': True,
                'conditional_programs': True,
                'hard_deadlines': True,
                'version_history': True,
                'accessibility': False,
                'automatic_control_detection': False,
            }
            reply['harness'].update(
                continuous_watch=True,
                svg_paths=True,
                file_outcome_checks=True,
                explicit_fragment_restore=True,
                target_revalidation=True,
                targeted_run_stop=True,
                structured_handoff=True,
            )
        return reply
    if args.command == 'set':
        windows = client.windows()
        if bool(args.window) == bool(args.title):
            raise ValueError('Provide exactly one window ID or --title')
        if args.title:
            matches = [w for w in windows if args.title.casefold() in w.get('title', '').casefold()]
            if len(matches) != 1:
                choices = [{'id': w['id'], 'title': w.get('title', '')} for w in matches]
                raise ValueError(
                    f'--title matched {len(matches)} windows; use a more specific title or exact ID. Matches: {choices}'
                )
            identity = matches[0]['id']
        else:
            identity = args.window.strip('{}')
        return {
            'ok': True,
            **Workspace(args.window_dir, args.output_dir).assign(identity, args.name, windows),
        }
    if args.command == 'capture':
        if 'capture' not in client.request('capabilities').get('operations', []):
            raise ValueError('This backend does not support window capture')
        return client.request(
            'capture',
            window=client.window_store.resolve_window(args.window.strip('{}')),
            path=str(Path(args.output).resolve()),
        )
    if args.command == 'clipboard':
        if client.lane != 'host':
            raise ValueError('Clipboard access requires explicit --host')
        if args.action == 'get':
            if args.text is not None:
                raise ValueError('clipboard get takes no text argument')
            return {'ok': True, 'text': client.clipboard_get(), 'lane': 'host'}
        text = args.text if args.text is not None else sys.stdin.read(8193)
        return client.clipboard_set(text)
    from .runtime import Context

    ctx = Context(client, args.window, Workspace(args.window_dir, args.output_dir))
    if args.command in ('click', 'move', 'scroll'):
        x, y = args.x, args.y
        if args.absolute:
            window = ctx.window
            x, y = x - window['x'], y - window['y']
        point = {'x': x, 'y': y}
        if args.command == 'click':
            ctx.click(button=args.button, **point)
        elif args.command == 'move':
            ctx.move(**point)
        else:
            ctx.scroll(args.delta, axis=args.axis, **point)
    elif args.command == 'focus':
        ctx.focus()
    elif args.command in ('type', 'paste'):
        ctx.paste(args.text, shortcut=args.shortcut)
    elif args.command == 'key':
        ctx.press(args.chord, duration=args.duration)
    return {
        'ok': True,
        'command': args.command,
        'window': ctx.window_id,
        'status': 'dispatched',
        'released': True,
    }


def main(argv=None):
    argument_parser = parser()
    args, extra = argument_parser.parse_known_args(argv)
    if extra and not (args.command == 'fragments' and args.fragment_action == 'run'):
        argument_parser.error('unrecognized arguments: ' + ' '.join(extra))
    for name, default in (
        ('socket', None),
        ('deadline', 120),
        ('trace', None),
        ('window_dir', None),
        ('output_dir', None),
        ('budget', 20000),
        ('lane', 'agent'),
    ):
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
        if args.command == 'doctor':
            from .diagnostics import diagnose

            reply = diagnose(args)
            print(json.dumps(reply, indent=2))
            return 0 if reply['ok'] else 1
        from .commands import local_command, run_program

        reply = local_command(args, extra)
        if reply is not None:
            print(json.dumps(reply, indent=2))
            return 0
        if args.command in ('execute', 'draw') or (
            args.command == 'fragments' and args.fragment_action == 'run'
        ):
            reply = run_program(args, extra, socket_path(args.socket))
            print(json.dumps(reply, indent=2))
            return 0 if reply['ok'] else 1
        if args.command in ('type', 'paste'):
            Client.validate_text(args.text)
        path = socket_path(args.socket)
        try:
            client = Client(
                path,
                deadline=args.deadline,
                action_budget=args.budget,
                lane=args.lane,
                window_dir=args.window_dir,
            )
        except OSError as error:
            raise ConnectionError(
                f'Cannot connect to the agent compositor at {path}: {error.strerror}. Set CA_SOCKET or use --socket for your test session.'
            ) from None
        try:
            with client:
                if args.command == 'watch':
                    from .watch import watch

                    watch(
                        client,
                        args,
                        lambda event: print(json.dumps(event, allow_nan=False), flush=True),
                    )
                    return 0
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
