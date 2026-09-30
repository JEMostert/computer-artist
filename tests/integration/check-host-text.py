#!/usr/bin/env python3
"""Clipboard and keyboard delivery to real Qt Wayland clients in private KWin."""

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root))
from computer_artist.client import ActionError, Client

s = json.loads(Path(sys.argv[1]).read_text())
assert s['compositor'] == '/usr/bin/kwin_wayland'
assert '/ca-stock-' in s['wayland'] and s['wayland'].endswith('/wayland-test')
out = Path(s['output'])
env = dict(
    os.environ,
    CA_SOCKET=s['control'],
    CA_WINDOW_DIR=str(out / 'text-window'),
    CA_OUTPUT_DIR=str(out / 'text-output'),
)
report = {}


def wait(test, timeout=4):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if test():
            return
        time.sleep(0.02)
    raise AssertionError('condition timed out')


def human(*args):
    subprocess.run(
        [str(root / 'build/stock-test-input'), s['wayland'], *map(str, args)], check=True
    )


def ca(*args, source=None, success=True):
    result = subprocess.run(
        [str(root / 'ca'), *args], input=source, text=True, capture_output=True, env=env, timeout=15
    )
    assert (result.returncode == 0) == success, (result.stdout, result.stderr)
    return json.loads(result.stdout or result.stderr)


def events():
    return [json.loads(line) for line in (out / 'human-events.jsonl').read_text().splitlines()]


def text():
    file = out / 'human-text.txt'
    return file.read_text() if file.exists() else ''


with Client(s['control']) as observer, Client(s['control'], lane='host') as host:
    wait(lambda: host.request('capabilities')['clipboard'])
    caps = observer.request('capabilities')
    assert caps['keyboard'] and not caps['clipboard']
    for op in ('key', 'clipboard_get', 'clipboard_set'):
        try:
            observer.request(op, code=30, pressed=True, text='must not write')
        except ActionError:
            pass
        else:
            raise AssertionError('Agent lane accepted ' + op)
    observer.request('session_close')
    host.clipboard_get()
    assert not observer.request('session_status')['session']
    sample = 'Hello café — 日本語 😀\nSecond line\tend'
    ca('--host', 'clipboard', 'set', sample)
    assert host.clipboard_get() == sample
    assert not observer.request('session_status')['session']
    report['clipboard_unicode_survives_cli_disconnect_without_focus_or_session'] = True
    windows = observer.windows()
    target = next(w for w in windows if w['title'] == 'Your typing space')
    other = next(w for w in windows if w['title'] == 'Agent canvas')
    identity = target['id']
    host.acquire(identity)
    host.focus(identity)
    released = sum(e['event'] == 'release' for e in events())
    host.click(target['x'] + 200, target['y'] + 300)
    # Dispatch acknowledgement is not application completion. Qt batches
    # pointer frames independently of keys; wait until its click handler ran
    # before selecting text, or the pending release may alter that selection.
    wait(lambda: sum(e['event'] == 'release' for e in events()) > released)
    host.chord(29, 30)
    host.chord(14)
    before = observer.request('session_status')['host_position']
    host.paste(sample)
    wait(lambda: text() == sample)
    assert observer.request('session_status')['host_position'] == before
    assert host.clipboard_get() == sample
    report['unicode_multiline_paste_delivered_without_pointer_motion'] = True
    # Read data owned by another real Wayland application, not our data source.
    host.chord(29, 30)
    host.chord(29, 46)
    wait(lambda: host.clipboard_get() == sample)
    host.chord(14)
    host.chord(30, duration=0.8)
    wait(lambda: any(e['event'] == 'key_down' and e.get('repeat') for e in events()))
    assert observer.request('session_status')['lanes']['host']['keys'] == 0
    report['application_copy_and_held_key_repeat'] = True
    host.release()
    # An unresponsive clipboard owner must not freeze the compositor.
    failures = []

    def stalled_read():
        try:
            host.clipboard_get()
        except ActionError as error:
            failures.append(error.reply['error'])

    os.kill(target['pid'], signal.SIGSTOP)
    reader = threading.Thread(target=stalled_read)
    try:
        reader.start()
        time.sleep(0.15)
        before_ping = time.monotonic()
        observer.request('ping')
        assert time.monotonic() - before_ping < 0.5
        reader.join(3)
        assert failures == ['clipboard_timeout'], failures
    finally:
        os.kill(target['pid'], signal.SIGCONT)
        reader.join(3)
    report['stalled_clipboard_owner_times_out_without_blocking_compositor'] = True
    ca('--host', 'key', '--window', identity, 'Ctrl+A')
    ca('--host', 'paste', '--window', identity, 'CLI café 😀')
    wait(lambda: text() == 'CLI café 😀')
    ca(
        '--host',
        'execute',
        '--window',
        identity,
        source="def run(ctx):\n    ctx.press('Ctrl+A')\n    ctx.paste('Runtime 日本語')\n    ctx.key_down('Shift')\n    ctx.key_down('Right')\n    ctx.sleep(.05)\n    ctx.key_up('Right')\n    ctx.key_up('Shift')\n",
    )
    wait(lambda: text() == 'Runtime 日本語')
    report['cli_and_runtime_paste_and_key_holds'] = True
    # Invalid input must not replace the clipboard or dispatch partial text.
    original = host.clipboard_get()
    ca('--host', 'paste', '--window', identity, '😀' * 2049, success=False)
    assert host.clipboard_get() == original
    try:
        host.request('clipboard_set', text='a' * 8193)
    except ActionError:
        pass
    else:
        raise AssertionError('Oversized clipboard accepted')
    assert host.clipboard_get() == original
    report['oversized_text_rejected_before_mutation'] = True

    def no_keys():
        return observer.request('session_status')['lanes']['host']['keys'] == 0

    # Cleanup must deliver release to the app on disconnect, stale generation,
    # watchdog expiry, session close and focus change.
    for reason in ('disconnect', 'stale', 'watchdog', 'close', 'focus'):
        count = sum(e['event'] == 'key_up' for e in events())
        if reason == 'watchdog':
            raw = socket.socket(socket.AF_UNIX)
            raw.connect(s['control'])
            stream = raw.makefile('rb')

            def request(op, **values):
                raw.sendall((json.dumps({'op': op, 'lane': 'host', **values}) + '\n').encode())
                return json.loads(stream.readline())

            lease = request('acquire', window=identity)
            token = {k: lease[k] for k in ('lease', 'generation')}
            assert request('focus', window=identity, **token)['ok']
            assert request('key', code=42, pressed=True, **token)['ok']
            wait(no_keys, 7)
            stream.close()
            raw.close()
        else:
            with Client(s['control'], lane='host') as held:
                held.acquire(identity)
                held.focus(identity)
                held.key(42, True)
                if reason == 'stale':
                    held.generation -= 1
                    try:
                        held.key(30, True)
                    except ActionError:
                        pass
                    else:
                        raise AssertionError('Stale key accepted')
                elif reason == 'close':
                    observer.request('session_close')
                elif reason == 'focus':
                    # Application activation through the private compositor's script interface.
                    script = out / 'text-focus.js'
                    script.write_text(
                        'workspace.windowList().forEach(w => { if (w.caption === "Agent canvas") workspace.activeWindow=w; });'
                    )
                    dbusenv = dict(os.environ, DBUS_SESSION_BUS_ADDRESS=s['bus'])
                    sid = subprocess.check_output(
                        [
                            'qdbus6',
                            'org.kde.KWin',
                            '/Scripting',
                            'org.kde.kwin.Scripting.loadScript',
                            str(script),
                        ],
                        env=dbusenv,
                        text=True,
                    ).strip()
                    subprocess.run(
                        [
                            'qdbus6',
                            'org.kde.KWin',
                            '/Scripting/Script' + sid,
                            'org.kde.kwin.Script.run',
                        ],
                        env=dbusenv,
                        check=True,
                    )
                    wait(no_keys)
        wait(no_keys)
        if reason != 'focus':
            wait(lambda: sum(e['event'] == 'key_up' for e in events()) > count)
        else:
            # Wayland focus leave resets the old client's key state; Qt need not
            # expose a key-up callback after the widget has lost focus.
            start_agent = len((out / 'agent-events.jsonl').read_text().splitlines())
            human('key', 30)
            wait(
                lambda: any(
                    e.get('text') == 'a' and e.get('modifiers') == 0
                    for e in [
                        json.loads(line)
                        for line in (out / 'agent-events.jsonl')
                        .read_text()
                        .splitlines()[start_agent:]
                    ]
                )
            )
        report[reason + '_releases_held_key'] = True
    # A physical A during an agent-held Shift must reach the app unshifted.
    start = len(events())
    with Client(s['control'], lane='host') as held:
        held.acquire(identity)
        held.focus(identity)
        held.key(42, True)
        human('key', 30)
        wait(no_keys)
    wait(
        lambda: any(
            e['event'] == 'key_down' and e.get('text') == 'a' and e['modifiers'] == 0
            for e in events()[start:]
        )
    )
    report['external_key_preempts_and_removes_synthetic_shift'] = True
    # Transfer a physically pressed key that automation was already holding.
    with Client(s['control'], lane='host') as held:
        held.acquire(identity)
        held.focus(identity)
        held.key(42, True)
        human('shift-a-b')
        wait(no_keys)
    wait(lambda: text().endswith('Ab'))
    report['same_key_transfers_to_human_until_physical_release'] = True
    # Exercise an installed application and verify a saved file independently.
    saved = out / 'kwrite-host-text.txt'
    saved.write_text('')
    appenv = dict(
        os.environ,
        WAYLAND_DISPLAY=s['wayland'],
        QT_QPA_PLATFORM='wayland',
        DBUS_SESSION_BUS_ADDRESS=s['bus'],
        XDG_CONFIG_HOME=str(Path(s['runtime']) / 'config'),
        QT_QPA_PLATFORMTHEME='generic',
        QT_NO_XDG_DESKTOP_PORTAL='1',
    )
    for name in ('DISPLAY', 'QT_PLUGIN_PATH', 'SESSION_MANAGER'):
        appenv.pop(name, None)
    with (out / 'kwrite.log').open('w') as log:
        app = subprocess.Popen(
            ['kwrite', str(saved)], env=appenv, stdout=log, stderr=subprocess.STDOUT
        )
        try:
            wait(lambda: any(w['pid'] == app.pid and w['visible'] for w in observer.windows()), 10)
            window = next(w for w in observer.windows() if w['pid'] == app.pid and w['visible'])
            expected = 'KWrite café 日本語 😀\nVerified save\n'
            procedure = ca(
                'fragments',
                'create',
                'save-text',
                source=(root / 'examples/fragments/save-text.py').read_text(),
            )['result']
            successful = ca(
                '--host',
                'fragments',
                'run',
                'save-text',
                '--window',
                window['id'],
                '--version',
                procedure['version'],
                '--args',
                json.dumps({'path': str(saved), 'text': expected}),
            )
            assert successful['status'] == 'verified'
            assert any(
                check['passed'] and check['evidence']['fresh'] for check in successful['checks']
            )
            report['reusable_save_text_example_verified_in_kwrite'] = True
            # Keep the independent application-output oracle: the program's
            # verification result alone is insufficient acceptance evidence.
            wait(lambda: saved.read_text() == expected)
            stale_program = (
                'def run(ctx):\n'
                f'    before = ctx.snapshot_file({str(saved)!r})\n'
                f"    ctx.verify_file('fresh_save_required', {str(saved)!r}, "
                f'kind="text", after=before, expected_text={expected!r}, '
                'timeout=.5, stable_for=.1, interval=.05)\n'
            )
            stale = ca(
                '--host', 'execute', '--window', window['id'], source=stale_program, success=False
            )
            assert stale['status'] == 'interrupted' and not stale['final_outcome_known']
            assert any(
                check['name'] == 'fresh_save_required' and not check['passed']
                for check in stale['checks']
            )
            assert saved.read_text() == expected
            report['kwrite_actual_saved_file_named_verified_outcome'] = True
            report['stale_saved_file_is_interrupted_with_named_failed_evidence'] = True
            # Review both retained outcomes with an intentionally nonexistent
            # compositor endpoint. Evidence review cannot replay desktop input.
            control = env['CA_SOCKET']
            env['CA_SOCKET'] = str(out / 'offline-review-no-socket')
            try:
                for execution in (successful, stale):
                    dossier = ca('runs', 'inspect', execution['run_id'])
                    assert dossier['result']['status'] == execution['status']
                    assert dossier['result']['checks'] == execution['checks']
            finally:
                env['CA_SOCKET'] = control
            report['saved_outcome_inspection_works_offline'] = True
            report['kwrite_unicode_paste_and_shortcut_save_verified_from_file'] = True
        finally:
            app.terminate()
            try:
                app.wait(5)
            except subprocess.TimeoutExpired:
                app.kill()
                app.wait()
    # Plugin unload releases held keys too.
    with Client(s['control'], lane='host') as held:
        held.acquire(identity)
        held.focus(identity)
        held.key(42, True)
        count = sum(e['event'] == 'key_up' for e in events())
        subprocess.run(
            [
                'qdbus6',
                'org.kde.KWin',
                '/Plugins',
                'org.kde.KWin.Plugins.UnloadPlugin',
                'computerartist',
            ],
            env=dict(os.environ, DBUS_SESSION_BUS_ADDRESS=s['bus']),
            check=True,
        )
        wait(lambda: sum(e['event'] == 'key_up' for e in events()) > count)
    report['unload_releases_held_key'] = True
report['passed'] = True
(out / 'host-text-regression.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
