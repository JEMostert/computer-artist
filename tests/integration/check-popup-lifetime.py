#!/usr/bin/env python3
"""Popup role/window teardown and release in an isolated packaged KWin."""

import json
import os
import signal
import socket
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path

from harness import human, script, wait

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from computer_artist.client import Client

state = json.loads(Path(sys.argv[1]).read_text())
assert '/ca-stock-' in state['wayland'] and state['wayland'].endswith('/wayland-test')
out = Path(state['output'])
env = dict(os.environ, DBUS_SESSION_BUS_ADDRESS=state['bus'])
report = {}


def command(value):
    path = out / 'agent-popup-command'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(value)
    temporary.replace(path)
    wait(lambda: not path.exists())


with ExitStack() as stack:
    observer = stack.enter_context(Client(state['control']))

    def popups():
        return [w for w in observer.windows() if w['role'] == 'popup']

    def idle():
        lane = observer.request('session_status')['lanes']['agent']
        return not lane['busy'] and lane['keys'] == 0 and lane['buttons'] == 0

    def prepare():
        script(
            env,
            out,
            'popup-focus.js',
            'for(const w of workspace.windowList()) { '
            'if(w.caption==="Your typing space") workspace.activeWindow=w; '
            'if(w.caption==="Agent canvas") w.frameGeometry={x:560,y:40,width:790,height:770}; }',
        )
        human(state, 'move', 200, 250)
        wait(
            lambda: any(
                w['title'] == 'Agent canvas' and w['acquirable'] for w in observer.windows()
            )
        )
        return next(w for w in observer.windows() if w['title'] == 'Agent canvas')

    def open_nested(owner, canvas):
        owner.acquire(canvas['id'])
        owner.move(canvas['x'] + 180, canvas['y'] + 250)
        owner.button(code=273, pressed=True)
        owner.button(code=273, pressed=False)
        wait(lambda: len(popups()) == 1)
        command('nested')
        wait(lambda: len(popups()) == 2)
        menus = popups()
        assert all(not w['popup_grab'] for w in menus)
        assert {w['parent'] for w in menus} == {
            canvas['id'],
            next(w['id'] for w in menus if w['parent'] == canvas['id']),
        }

    # Normal release must still dismiss live roles, child before parent, and
    # clear held input before any popup destruction can reenter release().
    for mode in (
        'release-live',
        'release',
        'disconnect',
        'takeover',
        'unload',
        'destroy-popups',
        'close-parent',
        'kill-client',
    ):
        canvas = prepare()
        owner = Client(state['control'])
        try:
            print('Testing popup lifetime:', mode, flush=True)
            open_nested(owner, canvas)
            popup = popups()[-1]
            owner.move(popup['x'] + 1, popup['y'] + 1)
            if mode != 'release-live':
                owner.button(pressed=True)
            owner.key(42, True)
            if mode in ('release', 'release-live'):
                owner.release()
            elif mode == 'disconnect':
                owner.socket.shutdown(socket.SHUT_RDWR)
            elif mode == 'takeover':
                human(state, 'move', canvas['x'] + 40, canvas['y'] + 200)
            elif mode == 'unload':
                subprocess.run(
                    [
                        'qdbus6',
                        'org.kde.KWin',
                        '/Plugins',
                        'org.kde.KWin.Plugins.UnloadPlugin',
                        'computerartist',
                    ],
                    env=env,
                    check=True,
                )
                observer.close()
                result = subprocess.check_output(
                    [
                        'qdbus6',
                        'org.kde.KWin',
                        '/Plugins',
                        'org.kde.KWin.Plugins.LoadPlugin',
                        'computerartist',
                    ],
                    env=env,
                    text=True,
                )
                assert result.strip() == 'true'
                observer = stack.enter_context(Client(state['control']))
            elif mode == 'kill-client':
                os.kill(canvas['pid'], signal.SIGKILL)
            else:
                command(mode)
                if mode == 'destroy-popups':
                    wait(lambda: not popups())
                    # Retained deleted popup windows used to remain in CA's list
                    # and were dismissed again when this toplevel closed.
                    command('close-parent')
            wait(idle)
            wait(lambda: not popups())
            assert observer.request('ping')['ok']
            if mode in ('release', 'release-live', 'disconnect', 'takeover'):
                reason = observer.request('session_status')['lanes']['agent']['stop_reason']
                assert (
                    reason
                    == {
                        'release': 'released',
                        'release-live': 'released',
                        'disconnect': 'disconnect',
                        'takeover': 'human_pointer_entered',
                    }[mode]
                ), reason
            assert (
                next(w for w in observer.windows() if w['human_active'])['title']
                == 'Your typing space'
            )
            report[mode] = True
        finally:
            owner.close()
        if mode in ('close-parent', 'destroy-popups'):
            command('show-parent')
    report['passed'] = True
(out / 'popup-lifetime-report.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report), flush=True)
