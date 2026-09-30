#!/usr/bin/env python3
"""Verify independent native keyboard in a private packaged-KWin harness only."""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root))
from computer_artist.client import Client

state = json.loads(Path(sys.argv[1]).read_text())
assert state['compositor'] == '/usr/bin/kwin_wayland'
assert '/ca-stock-' in state['wayland'] and state['wayland'].endswith('/wayland-test')
out = Path(state['output'])
env = dict(
    os.environ,
    WAYLAND_DISPLAY=state['wayland'],
    QT_QPA_PLATFORM='wayland',
    DBUS_SESSION_BUS_ADDRESS=state['bus'],
)
env.pop('DISPLAY', None)
env.pop('QT_PLUGIN_PATH', None)
report = {}


def wait(test, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if test():
            return
        time.sleep(0.03)
    raise AssertionError('condition timed out')


def human(*args):
    subprocess.run(
        [
            os.environ.get('CA_TEST_INPUT', str(root / 'build/stock-test-input')),
            state['wayland'],
            *map(str, args),
        ],
        check=True,
    )


def script(source):
    path = out / 'keyboard-arrange.js'
    path.write_text(source)
    sid = subprocess.check_output(
        ['qdbus6', 'org.kde.KWin', '/Scripting', 'org.kde.kwin.Scripting.loadScript', str(path)],
        env=env,
        text=True,
    ).strip()
    subprocess.run(
        ['qdbus6', 'org.kde.KWin', '/Scripting/Script' + sid, 'org.kde.kwin.Script.run'],
        env=env,
        check=True,
    )


def press(client, *codes):
    client.chord(*codes)


document = out / 'independent-document.txt'
document.write_text('')
log = (out / 'independent-kwrite.log').open('w')
kwrite = subprocess.Popen(['kwrite', str(document)], env=env, stdout=log, stderr=subprocess.STDOUT)
try:
    with Client(state['control']) as observer, Client(state['control']) as agent:
        assert observer.request('capabilities')['keyboard'] is True
        wait(lambda: any(w['pid'] == kwrite.pid for w in observer.windows()))
        target = next(w for w in observer.windows() if w['pid'] == kwrite.pid)
        human_window = next(w for w in observer.windows() if w['title'] == 'Your typing space')
        script(
            'for (const w of workspace.windowList()) {\n'
            ' if(w.caption==="Agent canvas") w.minimized=true;\n'
            ' if(w.pid==='
            + str(kwrite.pid)
            + ') w.frameGeometry={x:560,y:40,width:790,height:770};\n'
            ' if(w.caption==="Your typing space") workspace.activeWindow=w;\n}'
        )
        time.sleep(0.3)
        human('move', human_window['x'] + 200, human_window['y'] + 300)
        human('click')
        wait(
            lambda: next(w for w in observer.windows() if w['id'] == human_window['id'])[
                'human_active'
            ]
        )
        target = next(w for w in observer.windows() if w['pid'] == kwrite.pid)
        agent.acquire(target['id'])
        agent.click(target['x'] + 200, target['y'] + 240)
        time.sleep(0.15)
        # Hold agent Shift while a separate device types lowercase into human app.
        agent.request('key', code=42, pressed=True)
        human('key', 48)
        press(agent, 30)
        agent.request('key', code=42, pressed=False)
        # Human Shift must not uppercase the agent's next lowercase letter.
        human_shift = subprocess.Popen(
            [
                os.environ.get('CA_TEST_INPUT', str(root / 'build/stock-test-input')),
                state['wayland'],
                'shift-wait',
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert human_shift.stdout.readline().strip() == 'held'
            press(agent, 48)
            human_shift.wait(3)
            assert human_shift.returncode == 0
        finally:
            if human_shift.poll() is None:
                human_shift.kill()
                human_shift.wait()
            human_shift.stdout.close()
        typing = threading.Thread(target=lambda: [human('shift-a-b') for _ in range(4)])
        typing.start()
        for _ in range(12):
            press(agent, 48)
            time.sleep(0.04)
        typing.join()
        press(agent, 29, 31)
        wait(lambda: document.read_text() == 'A' + 'b' * 13 + '\n')
        wait(lambda: (out / 'human-text.txt').read_text() == 'bA' + 'Ab' * 4)
        windows = observer.windows()
        assert next(w for w in windows if w['id'] == human_window['id'])['human_active']
        report['existing_native_kwrite_save_and_simultaneous_human_typing'] = True
        report['independent_shift_and_keyboard_focus'] = True
        # A cancelled held modifier must not alter later agent text.
        agent.request('key', code=42, pressed=True)
        agent.request('cancel')
        press(agent, 46)
        press(agent, 29, 31)
        wait(lambda: document.read_text() == 'A' + 'b' * 13 + 'c\n')
        report['cancel_releases_held_modifier'] = True
        # A rejected chord reconciles held keys and keeps subsequent work usable.
        agent.request('key', code=42, pressed=True)
        from computer_artist.client import ActionError

        try:
            agent.request('key', code=0, pressed=True)
        except ActionError:
            pass
        else:
            raise AssertionError('Invalid key accepted')
        assert observer.request('session_status')['lanes']['agent']['keys'] == 0
        press(agent, 18)
        press(agent, 29, 31)
        wait(lambda: document.read_text() == 'A' + 'b' * 13 + 'ce\n')
        report['rejected_key_reconciles_held_modifier'] = True
        agent.release()
        # Raw connections deliberately have no heartbeat to test server watchdog.
        for reason in ('disconnect', 'watchdog'):
            raw = socket.socket(socket.AF_UNIX)
            raw.settimeout(2)
            raw.connect(state['control'])
            reader = raw.makefile('rb')

            def request(**values):
                raw.sendall((json.dumps(values) + '\n').encode())
                return json.loads(reader.readline())

            lease = request(op='acquire', window=target['id'])
            assert lease['ok']
            assert request(
                op='key',
                code=42,
                pressed=True,
                lease=lease['lease'],
                generation=lease['generation'],
            )['ok']
            if reason == 'disconnect':
                reader.close()
                raw.close()
            wait(lambda: not observer.request('session_status')['lanes']['agent']['busy'], 7)
            reader.close()
            raw.close()
            with Client(state['control']) as resumed:
                resumed.acquire(target['id'])
                # KWrite may discard its selected widget after a keyboard leave.
                # The recovery workflow explicitly selects the known editor.
                resumed.click(target['x'] + 200, target['y'] + 240)
                press(resumed, 29, 30)
                press(resumed, 33 if reason == 'disconnect' else 34)
                press(resumed, 29, 31)
                expected = ('f' if reason == 'disconnect' else 'g') + '\n'
                # Confirm the save while the application still owns agent focus.
                wait(lambda: document.read_text() == expected)
            report[reason + '_releases_held_modifier'] = True
        agent.acquire(target['id'])
        # Takeover with agent Shift held: human focus must receive no stale Shift.
        agent.request('key', code=42, pressed=True)
        human('move', target['x'] + 200, target['y'] + 240)
        human('click')
        human('key', 32)
        # Save through human focus using an independent device shortcut.
        # KWrite's live document is inspected through host only in separate harness.
        with Client(state['control'], lane='host') as host:
            host.acquire(target['id'])
            host.focus(target['id'])
            host.chord(29, 31)
            host.release()
        wait(lambda: 'd' in document.read_text() and 'D' not in document.read_text())
        assert not observer.request('session_status')['lanes']['agent']['busy']
        report['human_takeover_releases_agent_modifier'] = True
        # Exercise the full reusable Context/worker/file-verification workflow.
        script(
            'for(const w of workspace.windowList()) if(w.caption==="Your typing space") workspace.activeWindow=w;'
        )
        human('move', human_window['x'] + 200, human_window['y'] + 300)
        from computer_artist.supervisor import run_supervised
        from computer_artist.workspace import Workspace

        store = Workspace(out / 'workflow-window', out / 'workflow-output')
        store.fragments.write(
            'save-shortcut', (root / 'examples/fragments/save-shortcut.py').read_text()
        )
        result = run_supervised(
            {
                'socket': state['control'],
                'window': target['id'],
                'lane': 'agent',
                'window_root': str(store.root),
                'output_root': str(store.output),
                'deadline': 8,
                'budget': 400,
                'source': 'def run(ctx):\n'
                + f'    before = ctx.snapshot_file({str(document)!r})\n'
                + '    ctx.click(x=200,y=240)\n'
                + '    ctx.press("Ctrl+A")\n'
                + '    ctx.press("J")\n'
                + f'    ctx.fragments.call("save-shortcut", path={str(document)!r}, expected_text="j\\n")\n'
                + f'    ctx.verify_file("Requested document is freshly saved", {str(document)!r}, kind="text", after=before, expected_text="j\\n")\n'
                + '    ctx.observe()\n',
                'arguments': {},
            }
        )
        assert result['ok'] and result['status'] == 'verified', result
        assert document.read_text() == 'j\n'
        recorded = json.loads(Path(result['record']).read_text())
        assert all(e.get('lane') == 'agent' for e in recorded['trace'])
        assert not any(e.get('operation') == 'focus' for e in recorded['trace'])
        report['reusable_independent_save_and_outer_fresh_file_check'] = True
        report['verified_workflow_record'] = result['run_id']
        report['passed'] = True
    (out / 'independent-keyboard-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report), flush=True)
finally:
    kwrite.terminate()
    try:
        kwrite.wait(5)
    except subprocess.TimeoutExpired:
        kwrite.kill()
        kwrite.wait()
    log.close()
