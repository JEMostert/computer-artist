#!/usr/bin/env python3
"""Pointer separation/lifetime regression for the plugin in STOCK KWin only."""

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
from computer_artist.client import ActionError, Client

s = json.loads(Path(sys.argv[1]).read_text())
assert s['compositor'] == '/usr/bin/kwin_wayland'
assert '/ca-stock-' in s['wayland'] and s['wayland'].endswith('/wayland-test')
out = Path(s['output'])
env = dict(os.environ, DBUS_SESSION_BUS_ADDRESS=s['bus'])


def human(*args):
    subprocess.run(
        [str(root / 'build/stock-test-input'), s['wayland'], *map(str, args)], check=True
    )


def wait(test, timeout=3):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if test():
            return
        time.sleep(0.02)
    raise AssertionError('condition did not become true')


def events():
    return [json.loads(line) for line in (out / 'agent-events.jsonl').read_text().splitlines()]


def releases():
    return sum(e['event'] == 'release' for e in events())


def request(sock, **kwargs):
    sock.sendall((json.dumps(kwargs) + '\n').encode())
    return json.loads(sock.recv(65536))


human('move', 200, 250)
human('click')
time.sleep(0.2)
report = {'compositor': '/usr/bin/kwin_wayland', 'patched_compositor': False}
with Client(s['control']) as observer:
    assert not observer.request('session_status')['session']
    canvas = next(w for w in observer.windows() if w['title'] == 'Agent canvas')
    identity = canvas['id']
    x = canvas['x'] + 180
    y = canvas['y'] + 250
    human_window = next(w for w in observer.windows() if w['human_active'])
    human_id = human_window['id']
    assert not human_window['acquirable']
    assert 'human_keyboard_in_application' in human_window['agent_restrictions']
    assert 'human_pointer_in_application' in human_window['agent_restrictions']
    assert canvas['acquirable'] and canvas['host_acquirable']
    assert canvas['scale'] == 1 and canvas['output'] and not canvas['fullscreen']
    assert canvas['app_id'] == 'computer-artist-agent' or canvas['resource_class'], canvas
    report['window_scale_output_and_identity'] = True
    report['truthful_pointer_and_keyboard_readiness'] = True
    before = (out / 'human-text.txt').read_text() if (out / 'human-text.txt').exists() else ''
    observer.request('capture', window=identity, path=str(out / 'plugin-before.png'))
    original_capture = (out / 'plugin-before.png').read_bytes()
    try:
        observer.request('capture', window=identity, path=str(out / 'plugin-before.png'))
    except ActionError:
        pass
    else:
        raise AssertionError('Capture overwrote an existing destination')
    assert (out / 'plugin-before.png').read_bytes() == original_capture
    dangling = out / 'capture-dangling.png'
    dangling.symlink_to(out / 'missing-capture.png')
    try:
        observer.request('capture', window=identity, path=str(dangling))
    except ActionError:
        pass
    else:
        raise AssertionError('Capture replaced a dangling symlink')
    assert dangling.is_symlink() and not (out / 'missing-capture.png').exists()
    report['capture_existing_and_dangling_paths_preserved'] = True
    owner = Client(s['control'])
    with owner:
        with owner.owned(identity):
            owned_window = next(w for w in observer.windows() if w['id'] == identity)
            assert not owned_window['acquirable'] and not owned_window['host_acquirable']
            assert 'lane_busy' in owned_window['agent_restrictions']
            assert 'other_lane_owns_application' in owned_window['host_restrictions']
            report['truthful_lane_ownership_readiness'] = True
            worker = threading.Thread(target=lambda: [human('key', code) for code in (48, 46)])
            worker.start()
            owner.path(
                [(x + i, y + 40 * __import__('math').sin(i / 30)) for i in range(200)],
                interval=0.004,
            )
            worker.join()
    wait(lambda: (out / 'human-text.txt').read_text() == before + 'bc')
    assert next(w['id'] for w in observer.windows() if w['human_active']) == human_id
    assert not any(e['event'] == 'key' for e in events())
    assert observer.request('session_status')['cursor_visible']
    report['cursor_persists_after_release'] = True
    report['simultaneous_pointer_and_human_typing'] = True
    observer.request('capture', window=identity, path=str(out / 'plugin-after.png'))
    assert (out / 'plugin-before.png').read_bytes() != (out / 'plugin-after.png').read_bytes()
    report['rendered_result_changed'] = True
    owner = Client(s['control'])
    owner.acquire(identity)
    owner.move(x, y)
    owner.button(pressed=True)
    count = releases()
    owner.close()
    wait(lambda: releases() > count)
    report['disconnect_releases_button'] = True
    with Client(s['control']) as owner:
        owner.acquire(identity)
        owner.move(x, y)
        owner.button(pressed=True)
        count = releases()
        owner.generation -= 1
        try:
            owner.move(x + 1, y)
        except ActionError as error:
            assert error.reply['error'] == 'stale_lease_or_geometry'
        else:
            raise AssertionError('stale action accepted')
        wait(lambda: releases() > count)
    report['stale_action_cancels_drag'] = True
    with Client(s['control']) as owner:
        owner.acquire(identity)
        owner.move(x, y)
        owner.button(pressed=True)
        count = releases()
        observer.request('takeover')
        wait(lambda: releases() > count)
        assert not next(w for w in observer.windows() if w['id'] == identity)['agent']
        stopped = observer.request('session_status')['lanes']['agent']['stop_reason']
        assert stopped == 'explicit_stop', stopped
        assert owner.stop_reason() == 'explicit_stop'
    report['external_takeover'] = True
    report['agent_stop_reason_reported'] = True
    with Client(s['control']) as owner:
        owner.acquire(identity)
        owner.move(x, y)
        wheels = sum(e['event'] == 'wheel' for e in events())
        owner.scroll(15)
        wait(lambda: sum(e['event'] == 'wheel' for e in events()) > wheels)
        wheel = [e for e in events() if e['event'] == 'wheel'][-1]
        assert wheel['angle'] == -120, wheel
    report['complete_wheel_scroll_frame'] = True
    with Client(s['control']) as owner:
        owner.acquire(identity)
        owner.move(x, y)
        owner.button(pressed=True)
        count = releases()
        human('move', x + 80, y)
        wait(lambda: releases() > count)
        assert not next(w for w in observer.windows() if w['id'] == identity)['agent']
        stopped = observer.request('session_status')['lanes']['agent']['stop_reason']
        assert stopped == 'human_pointer_entered', stopped
    report['human_pointer_entry_takeover'] = True
    human('move', 200, 250)
    raw = socket.socket(socket.AF_UNIX)
    raw.settimeout(2)
    raw.connect(s['control'])
    lease = request(raw, op='acquire', window=identity)
    assert lease['ok']
    fields = {'lease': lease['lease'], 'generation': lease['generation']}
    assert request(raw, op='move', x=x, y=y, **fields)['ok']
    assert request(raw, op='button', code=272, pressed=True, **fields)['ok']
    count = releases()
    wait(lambda: not next(w for w in observer.windows() if w['id'] == identity)['agent'], timeout=7)
    wait(lambda: releases() > count)
    raw.close()
    assert observer.request('session_status')['cursor_visible']
    report['watchdog_despite_observer_polling'] = True
    with Client(s['control']) as owner:
        owner.acquire(identity)
        owner.move(x, y)
        owner.button(pressed=True)
        count = releases()
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
        wait(lambda: releases() > count)
    report['unload_releases_button'] = True
result = subprocess.check_output(
    ['qdbus6', 'org.kde.KWin', '/Plugins', 'org.kde.KWin.Plugins.LoadPlugin', 'computerartist'],
    env=env,
    text=True,
).strip()
assert result == 'true'
with Client(s['control']) as client:
    assert client.request('capabilities')['backend'] == 'stock_kwin_plugin'
    assert not client.request('session_status')['session']
    client.acquire(identity)
    client.move(x, y)
    client.button(pressed=True)
    count = releases()
    with Client(s['control']) as closer:
        state = closer.request('session_close')
        assert not state['session'] and not state['cursor_visible']
    wait(lambda: releases() > count)
report['explicit_session_close_releases_input_and_hides_cursor'] = True
with Client(s['control']) as observer:
    (out / 'agent-open-sibling').touch()
    wait(
        lambda: any(
            w['title'] == 'Agent sibling document' and w['human_active'] for w in observer.windows()
        )
    )
    original = next(w for w in observer.windows() if w['id'] == identity)
    assert not original['human_active'] and not original['acquirable']
    assert 'human_keyboard_in_application' in original['agent_restrictions']
    try:
        observer.acquire(identity)
    except ActionError:
        pass
    else:
        raise AssertionError('Acquired application with keyboard focus on sibling')
    (out / 'agent-close-sibling').touch()
    wait(lambda: not any(w['title'] == 'Agent sibling document' for w in observer.windows()))
    human('move', 200, 250)
    human('click')
    wait(lambda: next(w for w in observer.windows() if w['id'] == identity)['acquirable'])
report['same_client_sibling_keyboard_focus_refused'] = True
report.update(reload=True, passed=True)
(out / 'plugin-regression.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
