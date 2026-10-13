"""Exercise existing Windows features in disposable apps on one selected display."""

import argparse
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import unittest
import uuid
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from human_input import HumanInputMonitor
from windows_placement import verify_fixture_placement

from computer_artist.cli import execute
from computer_artist.client import Client
from computer_artist.control import request_stop
from computer_artist.runtime import Context
from computer_artist.supervisor import run_supervised
from computer_artist.windows import Win32
from computer_artist.windows_agent import MessageLane
from computer_artist.windows_commands import dispatch
from computer_artist.windows_monitors import contains, select_monitor
from computer_artist.workspace import Workspace

HUMAN_STOPS = {'human_pointer_moved', 'foreground_changed', 'human_focus_in_application'}


# Refusals that only a human's concurrent input can trigger in these disposable fixtures.
HUMAN_REFUSALS = (
    'human pointer moved',
    'foreground changed',
    'release held human keys',
    'human focus',
)


def held_keys(api):
    """Number of held keys/buttons; which keys a human holds is never recorded."""
    return sum(bool(api.user.GetAsyncKeyState(code) & 0x8000) for code in range(1, 256))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--monitor', required=True)
    parser.add_argument('--host-checks', action='store_true')
    parser.add_argument(
        '--placement-only',
        action='store_true',
        help='Inspect hidden fixture geometry, then stop without desktop input',
    )
    args = parser.parse_args()
    if args.placement_only:
        os.environ['CA_TEST_PLACEMENT_ONLY'] = '1'
    else:
        os.environ.pop('CA_TEST_PLACEMENT_ONLY', None)
    os.environ['CA_WINDOWS_MONITOR'] = args.monitor
    api = Win32()
    monitor = select_monitor(api, args.monitor)
    os.environ['CA_TEST_MONITOR_BOUNDS'] = json.dumps(monitor['bounds'])
    left, top, right, bottom = monitor['work']
    if right - left < 1150 or bottom - top < 600:
        raise ValueError('Selected monitor cannot fit the disposable fixtures')
    folder = ROOT / 'local' / 'windows-validation' / ('full-' + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True)
    store = Workspace(folder / 'window', folder / 'output')
    marker = uuid.uuid4().hex[:8]
    processes, targets = [], []
    foreground = api.user.GetForegroundWindow()
    cursor = wintypes.POINT()
    api.user.GetCursorPos(ctypes.byref(cursor))
    original_pointer = (cursor.x, cursor.y)
    results = {}
    stopped_marker = folder / 'isolated-stop.json'
    human = None

    harness_pointer = {'last': None}  # Where our own host input left the pointer.

    def check(name, operation, *, host=False):
        """Record passed, failed, skipped or interrupted_by_human with evidence.

        A failure is attributed to the human only when real (non-injected) input
        happened during that check and the failure itself is a human-takeover
        guard (or, for host typing, a readback mismatch while the human typed).
        Such checks are never counted as passed.
        """
        if host:
            until = time.monotonic() + 5
            while held_keys(api) and time.monotonic() < until:
                time.sleep(0.05)
        before = human.counts()
        held_before = held_keys(api)
        try:
            outcome = operation()
            record = dict(outcome) if isinstance(outcome, dict) else {'passed': outcome}
            record['passed'] = bool(record.get('passed'))
        except unittest.SkipTest as error:
            record = {'passed': None, 'status': 'skipped', 'skipped': str(error)}
        except Exception as error:
            record = {'passed': False, 'error': f'{type(error).__name__}: {error}'}
            if error.__context__ is not None:
                record['context'] = repr(error.__context__)
            if getattr(error, '__notes__', None):
                record['notes'] = list(error.__notes__)
        activity = human.since(before)
        record['human_input_events'] = activity
        if held_before:
            record['held_inputs_before'] = held_before
        if record['passed'] is False:
            record['held_inputs_after'] = held_keys(api)
            human_active = activity['keyboard'] + activity['mouse'] > 0
            text = record.get('error', '').lower()
            human_guard = record.get('stop_reason') in HUMAN_STOPS or any(
                phrase in text for phrase in HUMAN_REFUSALS
            )
            typed_over = host and 'actual' in record and activity['keyboard'] > 0
            if human_active and (human_guard or typed_over):
                record['passed'] = None
                record['status'] = 'interrupted_by_human'
        record.setdefault(
            'status', {True: 'passed', False: 'failed', None: 'skipped'}[record['passed']]
        )
        if host and not activity['mouse']:
            cursor_now = wintypes.POINT()
            api.user.GetCursorPos(ctypes.byref(cursor_now))
            harness_pointer['last'] = (cursor_now.x, cursor_now.y)
        results[name] = record
        print(json.dumps({name: record}), flush=True)

    def harness_foreground_returned():
        """Hand focus back only if one of our own fixtures still holds it."""
        current = api.user.GetForegroundWindow()
        fixtures = {int(w['id'].split('-')[1], 16) for w in targets}
        if current not in fixtures:
            return 'not_ours'
        if not foreground or not api.user.IsWindow(foreground):
            return 'original_foreground_gone'
        try:
            api.focus_host(foreground)
            return 'returned'
        except PermissionError as error:
            return f'refused: {error}'

    def connect(lane='agent', **options):
        client = Client('win32', lane=lane, deadline=15, window_dir=store.root, **options)
        # Scope stop/session-close tests to our marker, never another user's run.
        client.native_backend.stop_file = stopped_marker
        if lane == 'agent':

            def forbidden_input(_event):
                raise AssertionError('Independent input attempted to inject into the human seat')

            client.native_backend.api.send = forbidden_input
        return client

    def context(client, index=0):
        return Context(client, targets[index]['id'], store)

    def read(index=0):
        with connect() as client:
            return context(client, index).read_text()

    def cli(*arguments, source=None, ok=True):
        result = subprocess.run(
            [
                sys.executable,
                '-m',
                'computer_artist',
                '--monitor',
                args.monitor,
                '--window-dir',
                str(store.root),
                '--output-dir',
                str(store.output),
                *arguments,
            ],
            input=source,
            text=True,
            capture_output=True,
            timeout=8,
        )
        if result.returncode != (0 if ok else 1):
            raise AssertionError(result.stderr or result.stdout)
        return json.loads(result.stdout if ok else result.stderr)

    def events(index=1):
        """Complete JSON lines appended by the fixture; a partial last line is ignored."""
        path = folder / f'events-{index}.jsonl'
        for _attempt in range(10):
            try:
                lines = path.read_text(encoding='utf-8').split('\n') if path.exists() else []
                break
            except PermissionError:
                time.sleep(0.02)
        else:
            raise AssertionError(f'Event log {path.name} stayed locked')
        return [json.loads(line) for line in lines[:-1] if line]

    try:
        for index, x in enumerate((left + 60, left + 590)):
            processes.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        str(Path(__file__).with_name('windows_fixture.py')),
                        f'CA full {marker}-{index}',
                        str(x),
                        str(top + 70),
                        str(folder / f'events-{index}.jsonl'),
                    ],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    stdout=subprocess.PIPE if args.placement_only else None,
                )
            )
        if args.placement_only:
            hidden = [json.loads(process.stdout.readline()) for process in processes]
            print(
                json.dumps(
                    {
                        'monitor': monitor,
                        'hidden_fixtures': hidden,
                        'all_within_monitor': all(
                            contains(monitor, item['geometry']) for item in hidden
                        ),
                    },
                    indent=2,
                )
            )
            for item in hidden:
                MessageLane(api, item['hwnd']).message(item['hwnd'], 0x0010)
            return 0 if all(contains(monitor, item['geometry']) for item in hidden) else 1
        with connect() as observer:
            until = time.monotonic() + 5
            while time.monotonic() < until:
                targets = [
                    w for w in observer.windows() if w['title'].startswith(f'CA full {marker}')
                ]
                if len(targets) == 2:
                    break
                time.sleep(0.05)
            if len(targets) != 2:
                raise ValueError('Disposable test windows did not appear')
            time.sleep(0.5)  # Let any shell repositioning after display settle, then re-read.
            targets = sorted(
                (w for w in observer.windows() if w['title'].startswith(f'CA full {marker}')),
                key=lambda window: window['title'],
            )
            if len(targets) != 2:
                raise ValueError('Disposable test windows disappeared before input')
            verify_fixture_placement(monitor, targets)
            print(
                json.dumps({'verified_fixture_placement': targets, 'monitor': monitor}), flush=True
            )
        human = HumanInputMonitor().__enter__()
        run_start = human.counts()

        def editing():
            with connect() as client:
                ctx = context(client)
                ctx.write('first')
                if ctx.read_text() != 'first':
                    raise AssertionError('Initial text was not inserted')
                ctx.press('Ctrl+Z')
                if ctx.read_text() != '':
                    raise AssertionError('Undo did not remove the initial insertion')
                ctx.write('first')
                ctx.press('Ctrl+A')
                ctx.write('replacement')
                if ctx.read_text() != 'replacement':
                    raise AssertionError('Select-all did not replace the original text')
                ctx.press('Ctrl+A')
                ctx.write('x')
                ctx.press('End')
                ctx.key_down('Shift')
                ctx.press('a')
                ctx.key_up('Shift')
                ctx.press('Backspace')
                if ctx.read_text() != 'x':
                    raise AssertionError(
                        f'Local modifier/navigation/backspace produced {ctx.read_text()!r}, expected x'
                    )
                ctx.write('caf\u00e9 \u20ac \U0001f3a8\nsecond line')
                actual = ctx.read_text().replace('\r\n', '\n')
                expected = 'xcaf\u00e9 \u20ac \U0001f3a8\nsecond line'
                if actual != expected:
                    raise AssertionError(f'Unicode readback {actual!r}, expected {expected!r}')
                return True

        check('independent_select_undo_navigation_modifiers_unicode', editing)

        def clipboard_denial():
            with connect() as client:
                try:
                    client.clipboard_get()
                except ValueError:
                    return True
            return False

        check('independent_clipboard_refused', clipboard_denial)

        def control_refusals():
            with connect() as client:
                ctx = context(client)
                control = MessageLane(api, int(targets[0]['id'].split('-')[1], 16)).edit()
                lane = MessageLane(api, int(targets[0]['id'].split('-')[1], 16))
                lane.message(control, 0x00CF, 1)  # EM_SETREADONLY in our disposable editor.
                try:
                    try:
                        ctx.write('must not write')
                        return False
                    except ValueError:
                        pass
                    return ctx.read_text() == read()
                finally:
                    lane.message(control, 0x00CF, 0)

        check('readonly_control_refused_and_readable', control_refusals)

        def unsupported():
            before = len(events(0))
            if not before:
                raise AssertionError('Fixture event log is unavailable; cannot prove no fallback')
            with connect() as client:
                ctx = context(client)
                try:
                    ctx.click(x=50, y=170)  # Our custom canvas has no independent-control support.
                except ValueError:
                    time.sleep(0.2)  # Any wrongly injected click would arrive asynchronously.
                    return not any(
                        event['kind']
                        in {
                            'left_down',
                            'right_down',
                            'middle_down',
                            'vertical_wheel',
                            'horizontal_wheel',
                        }
                        for event in events(0)[before:]
                    )
            return False

        check('unsupported_canvas_no_host_fallback', unsupported)

        def ownership():
            with connect() as first:
                first.acquire(targets[0]['id'])
                with connect() as second:
                    try:
                        second.acquire(targets[0]['id'])
                    except PermissionError:
                        return True
            return False

        check('conflicting_application_lease_refused', ownership)

        def stop_and_restart():
            with connect() as active:
                active.acquire(targets[0]['id'])
                if not active.request('session_status')['lanes']['agent']['busy']:
                    return False
                with connect() as stopper:
                    execute(stopper, argparse.Namespace(command='stop'))
                try:
                    active.request('ping')
                    return False
                except Exception:
                    if active.stop_reason() != 'explicit_stop':
                        return False
            with connect() as fresh:
                fresh.acquire(targets[0]['id'])
                fresh.request('session_close')
                return not fresh.native_backend.lease

        check('stop_session_close_and_fresh_acquisition', stop_and_restart)

        def placement():
            ns = argparse.Namespace(
                command='window',
                action='move',
                socket=None,
                window=targets[0]['id'],
                window_dir=str(store.root),
                output_dir=str(store.output),
                x=left + 70,
                y=top + 80,
                width=480,
                height=480,
            )
            reply = dispatch(ns)
            if not reply['foreground_preserved']:
                return False
            ns.x = right - 100  # Test rejection before any cross-display move happens.
            try:
                dispatch(ns)
                return False
            except PermissionError:
                return True

        check('window_move_and_monitor_boundary_refusal', placement)

        def captures_and_targets():
            with connect() as client:
                ctx = context(client)
                before = ctx.observe(region=(10, 10, 300, 120))
                from PIL import Image

                with Image.open(before['image']) as image:
                    if image.size != (300, 120):
                        return False
                ctx.target('button', observation=before['id'], rect=(20, 90, 220, 35))
                ctx.click(target='@button')
                result = ctx.wait_for({'clicked': {'title_contains': 'clicked=1'}}, timeout=1)
                if result['matches'] != 'clicked':
                    return False
            capture = folder / 'capture.png'
            cli('capture', '--window', targets[0]['id'], str(capture))
            original = capture.read_bytes()
            cli('capture', '--window', targets[0]['id'], str(capture), ok=False)
            return (
                capture.read_bytes() == original
                and cli('files', 'inspect', str(capture), '--kind', 'image')['ok']
            )

        check('capture_crop_named_target_wait_and_no_overwrite', captures_and_targets)

        def watch_stream():
            result = subprocess.run(
                [
                    sys.executable,
                    '-m',
                    'computer_artist',
                    '--monitor',
                    args.monitor,
                    '--window-dir',
                    str(store.root),
                    '--output-dir',
                    str(store.output),
                    'watch',
                    '--window',
                    targets[0]['id'],
                    '--duration',
                    '.45',
                    '--interval',
                    '.1',
                    '--changes-only',
                ],
                text=True,
                capture_output=True,
                timeout=5,
            )
            if result.returncode:
                raise AssertionError(result.stderr)
            records = [json.loads(line) for line in result.stdout.splitlines()]
            return (
                records[0]['event'] == 'observation'
                and records[-1]['event'] == 'watch_finished'
                and records[-1]['frames'] >= 2
            )

        check('watch_jsonl_and_changes_only', watch_stream)

        def fragments():
            first = cli(
                'fragments',
                'create',
                'reader',
                source='def run(ctx):\n    ctx.verify("readable", bool(ctx.read_text()))\n    return "v1"',
            )
            version = first['result']['version']
            if (
                cli('fragments', 'run', 'reader', '--window', targets[0]['id'])['status']
                != 'verified'
            ):
                return False
            cli(
                'fragments',
                'update',
                'reader',
                source='def run(ctx):\n    ctx.verify("readable", bool(ctx.read_text()))\n    return "v2"',
            )
            if cli('fragments', 'run', 'reader', '--window', targets[0]['id'])['result'] != 'v2':
                return False
            cli('fragments', 'restore', 'reader', '--version', version)
            if cli('fragments', 'run', 'reader', '--window', targets[0]['id'])['result'] != 'v1':
                return False
            if len(cli('fragments', 'history', 'reader')['result']) != 2:
                return False
            cli('fragments', 'inspect', 'reader')
            cli('fragments', 'remove', 'reader')
            return cli('fragments', 'list')['result'] == []

        check('fragment_create_update_versions_run_restore_archive', fragments)

        spec = {
            'socket': 'win32',
            'window': targets[0]['id'],
            'window_root': str(store.root),
            'output_root': str(store.output),
            'deadline': 3,
            'budget': 1000,
            'lane': 'agent',
        }

        def cancelled_run():
            failures = []
            existing = set(store.output.glob('*/checkpoint.json'))

            def cancel():
                until = time.monotonic() + 2
                while time.monotonic() < until:
                    for path in store.output.glob('*/checkpoint.json'):
                        if path in existing:
                            continue
                        if json.loads(path.read_text()).get('checks'):
                            request_stop(store.output, path.parent.name)
                            return
                    time.sleep(0.02)
                failures.append('No cancellable checkpoint appeared')

            thread = threading.Thread(target=cancel)
            thread.start()
            result = run_supervised(
                {
                    **spec,
                    'source': 'def run(ctx):\n    ctx.verify("started",True)\n    while True: pass',
                }
            )
            thread.join(timeout=3)
            return (
                not failures
                and result.get('interruption', {}).get('code') == 'user_cancelled'
                and bool(result.get('checks'))
            )

        check('targeted_runs_stop_preserves_evidence', cancelled_run)

        def budgets():
            result = run_supervised(
                {**spec, 'budget': 4, 'source': 'def run(ctx):\n    while True: ctx.window'}
            )
            if result['ok'] or result.get('status') != 'interrupted':
                return False
            inspected = cli('runs', 'inspect', result['run_id'])
            cli('storage', 'keep', result['run_id'])
            status = cli('storage', 'status')
            if not any(
                run['id'] == result['run_id'] and run['preserved'] for run in status['runs']
            ):
                return False
            cli('storage', 'clean', '--dry-run')
            cli('storage', 'unkeep', result['run_id'])
            return inspected['ok'] and bool(cli('runs', 'list')['result'])

        check('action_budget_run_review_and_storage_preservation', budgets)

        api.user.GetCursorPos(ctypes.byref(cursor))
        injected = [
            name
            for name, result in results.items()
            if 'attempted to inject into the human seat' in result.get('error', '')
        ]
        independent_ran = any(result['status'] == 'passed' for result in results.values())
        results['independent_no_human_input_injection'] = {
            'passed': independent_ran and not injected,
            'status': 'passed' if independent_ran and not injected else 'failed',
            'injection_attempts_in': injected,
            'pointer_moved_during_test': (cursor.x, cursor.y) != original_pointer,
            'note': 'SendInput raises in every independent connection; human movement is allowed.',
        }
        current = api.user.GetForegroundWindow()
        fixture_pids = {w['pid'] for w in targets}
        human_activity = human.since(run_start)
        if current == foreground:
            status = 'passed'
        elif current and api.identity(current)[1] in fixture_pids:
            status = 'failed'  # Focus in a fixture without host input is a regression.
        elif human_activity['keyboard'] + human_activity['mouse']:
            status = 'interrupted_by_human'  # The human switched windows; allowed.
        else:
            status = 'failed'
        results['independent_human_foreground_preserved'] = {
            'passed': {'passed': True, 'failed': False}.get(status),
            'status': status,
            'human_input_events': human_activity,
        }

        def geometry_revocation():
            # Runs before any host check, so no fixture has been given focus by the harness.
            with connect() as client:
                client.acquire(targets[1]['id'])
                move = argparse.Namespace(
                    command='window',
                    action='move',
                    socket=None,
                    window=targets[1]['id'],
                    window_dir=str(store.root),
                    output_dir=str(store.output),
                    x=left + 600,
                    y=top + 90,
                    width=480,
                    height=480,
                )
                dispatch(move)
                try:
                    client.request('ping')
                    return False
                except Exception:
                    reason = client.stop_reason()
                    return {'passed': reason == 'geometry_changed', 'stop_reason': reason}

        check('geometry_change_revokes_old_input', geometry_revocation)
        with connect() as observer:
            targets = sorted(
                (w for w in observer.windows() if w['title'].startswith(f'CA full {marker}')),
                key=lambda window: window['title'],
            )
        verify_fixture_placement(monitor, targets)

        if args.host_checks:

            def host_input():
                with connect('host') as client:
                    ctx = context(client, 1)
                    ctx.write('HOST', interval=0.02)
                    ctx.press('Ctrl+A')
                    ctx.write('actual keys')
                    time.sleep(0.1)
                    actual = ctx.read_text()
                    current = api.user.GetForegroundWindow()
                    return {
                        'passed': actual == 'actual keys',
                        'actual': actual,
                        'expected': 'actual keys',
                        'foreground_is_target': current == client.native_backend.target,
                        'stop_reason': client.native_backend.reason,
                        'trace': [
                            {key: item[key] for key in ('operation', 'ok', 'error')}
                            for item in list(client.trace)[-40:]
                            if not item.get('ok')
                        ],
                    }

            check('host_focus_physical_keys_and_layout', host_input, host=True)

            def host_clipboard():
                from windows_clipboard import ClipboardSnapshot

                with connect('host') as client:
                    ctx = context(client, 1)
                    ctx.focus()
                    with ClipboardSnapshot(
                        client.native_backend.api, owner=client.native_backend.target
                    ) as saved:
                        # Checkpoint right after each successful write. After a failed write
                        # the latest clipboard is preserved rather than risk overwriting a
                        # human copy (the restore failure is then reported as a note).
                        ctx.clipboard_set('clipboard € \U0001f3a8')
                        saved.checkpoint()
                        if ctx.clipboard_get() != 'clipboard € \U0001f3a8':
                            return False
                        with connect('host') as standalone:
                            standalone.clipboard_set('standalone €')
                            saved.checkpoint()
                            if standalone.clipboard_get() != 'standalone €':
                                return False
                        ctx.press('Ctrl+A')
                        ctx.paste('pasted café € \U0001f3a8')
                        saved.checkpoint()
                        time.sleep(0.1)
                        actual = ctx.read_text()
                    return {
                        'passed': actual == 'pasted café € \U0001f3a8',
                        'actual': actual,
                        'clipboard_restored': True,
                    }

            check('host_clipboard_unicode_paste_and_full_restoration', host_clipboard, host=True)

            def host_gestures():
                before = len(events())
                with connect('host') as client:
                    ctx = context(client, 1)
                    ctx.click(x=50, y=170)
                    ctx.double_click(x=60, y=180)
                    ctx.click(x=70, y=190, button='right')
                    ctx.click(x=80, y=200, button='middle')
                    ctx.drag((50, 180), (250, 260), spacing=12, interval=0.004)
                    ctx.svg_path(
                        'M50 200 L250 280 L350 210', spacing=12, interval=0.004, verify_change=True
                    )
                    ctx.scroll(1, x=60, y=170)
                    ctx.scroll(-1, axis='horizontal', x=60, y=170)
                    time.sleep(0.2)
                    observed = events()[before:]
                    kinds = {event['kind'] for event in observed}
                    expected = {
                        'left_down',
                        'left_up',
                        'left_double',
                        'right_down',
                        'right_up',
                        'middle_down',
                        'middle_up',
                        'vertical_wheel',
                        'horizontal_wheel',
                    }
                    return {
                        'passed': expected <= kinds
                        and any(event.get('dragging') for event in observed),
                        'missing_events': sorted(expected - kinds),
                    }

            check('host_clicks_drag_svg_and_scroll', host_gestures, host=True)

            def takeover_cleanup():
                with connect('host') as client:
                    ctx = context(client, 1)
                    ctx.move(x=60, y=180)
                    ctx.key_down('Ctrl')
                    client.button(272, True)
                    # Simulate the human taking the pointer back.
                    api.user.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
                    api.user.SetCursorPos(left + 10, top + 10)
                    try:
                        client.move(targets[1]['x'] + 80, targets[1]['y'] + 190)
                        return False
                    except Exception:
                        reason = client.stop_reason()
                        if reason != 'human_pointer_moved':
                            return {'passed': False, 'stop_reason': reason}
                    time.sleep(0.05)
                    return {
                        'passed': not client.native_backend.keys
                        and not client.native_backend.buttons
                        and not (
                            api.user.GetAsyncKeyState(0xA2) & 0x8000
                            or api.user.GetAsyncKeyState(1) & 0x8000
                        ),
                        'stop_reason': reason,
                    }

            check('human_pointer_takeover_releases_held_keys_buttons', takeover_cleanup, host=True)

            def focus_takeover():
                with connect('host') as client:
                    client.acquire(targets[1]['id'])
                    api.focus_host(int(targets[0]['id'].split('-')[1], 16))
                    try:
                        client.request('ping')
                        return False
                    except Exception:
                        reason = client.stop_reason()
                        return {'passed': reason == 'foreground_changed', 'stop_reason': reason}

            check('human_foreground_takeover_stops_host_input', focus_takeover, host=True)
            focus_after_host_checks = harness_foreground_returned()
        else:
            focus_after_host_checks = 'not_applicable'

        def closed_target():
            current = api.user.GetForegroundWindow()
            if current and api.identity(current)[1] == targets[1]['pid']:
                raise unittest.SkipTest(
                    'Precondition unmet: foreground is in fixture 1; focus was not taken back'
                )
            with connect() as client:
                client.acquire(targets[1]['id'])
                hwnd = int(targets[1]['id'].split('-')[1], 16)
                MessageLane(api, hwnd).message(hwnd, 0x0010)
                try:
                    client.request('ping')
                    return False
                except Exception:
                    reason = client.stop_reason()
                    if reason != 'target_closed':
                        return {'passed': False, 'stop_reason': reason}
            with connect() as fresh:
                try:
                    fresh.acquire(targets[1]['id'])
                    return False
                except ValueError:
                    return True

        check('closed_target_revoked_and_stale_id_refused', closed_target)

        statuses = [result['status'] for result in results.values()]
        report = {
            'ok': 'failed' not in statuses,
            'complete': all(status == 'passed' for status in statuses),
            'summary': {status: statuses.count(status) for status in sorted(set(statuses))},
            'checks': results,
            'monitor': monitor,
            'edid_selector': args.monitor,
            'initial_fixtures': targets,
            'host_checks': args.host_checks,
            'focus_after_host_checks': focus_after_host_checks,
            'record': str(folder / 'result.json'),
        }
        (folder / 'result.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report, indent=2))
        # 0: every check passed; 2: no failures but some were skipped/interrupted; 1: failures.
        return 0 if report['complete'] else 2 if report['ok'] else 1
    finally:
        # Focus is ours to return only if our host checks left it in one of our fixtures.
        focused_fixture = args.host_checks and api.user.GetForegroundWindow() in {
            int(window['id'].split('-')[1], 16) for window in targets
        }
        closing = human.counts() if human is not None else None
        for window in targets:
            try:
                hwnd = int(window['id'].split('-')[1], 16)
                MessageLane(api, hwnd).message(hwnd, 0x0010)
            except (OSError, ValueError):
                pass
        for process in processes:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait()
        human_meanwhile = (
            sum(human.since(closing).values()) if human is not None else 1
        )  # Unknown human activity counts as activity: then nothing is taken back.
        if human is not None:
            human.__exit__(None, None, None)
        # Restore only what this harness changed and still owns; never take it from the human.
        try:
            cursor_now = wintypes.POINT()
            api.user.GetCursorPos(ctypes.byref(cursor_now))
            if harness_pointer['last'] == (cursor_now.x, cursor_now.y):
                api.user.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
                api.user.SetCursorPos(*original_pointer)
        except OSError:
            pass
        if focused_fixture and not human_meanwhile and foreground and api.user.IsWindow(foreground):
            try:
                api.focus_host(foreground)
            except PermissionError:
                pass


if __name__ == '__main__':
    raise SystemExit(main())
