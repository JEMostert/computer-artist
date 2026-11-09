#!/usr/bin/env python3
"""End-to-end check of ca browser against two real Chromium instances.

Starts a localhost notes fixture and two headless Chrome for Testing/Chromium
processes with disposable profiles and the extension loaded, the way a user's
already-running browsers would be. Then it drives them only through the ca CLI.
A minimal DevTools client stands in for the human (trusted input, tab
switching); ca itself never uses DevTools. Nothing outside the temporary
folders is written: the native host is registered with --user-data-dir only.

Branded Google Chrome 137+ ignores --load-extension, so pass Chrome for Testing
or Chromium with --chrome (default: the newest Playwright Chrome for Testing).
"""

import argparse
import base64
import glob
import json
import os
import secrets
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zlib
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from browser_fixture import serve  # noqa: E402

RESULTS = []


def step(name, passed, **evidence):
    RESULTS.append({'check': name, 'passed': bool(passed), **evidence})
    print(('PASS ' if passed else 'FAIL ') + name, json.dumps(evidence)[:300], flush=True)


def ca(*args, env, expect_ok=None):
    done = subprocess.run(
        [sys.executable, str(ROOT / 'ca'), 'browser', *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=150,
    )
    try:
        reply = json.loads(done.stdout)
    except ValueError:
        reply = {'ok': False, 'error': 'no_json', 'stdout': done.stdout, 'stderr': done.stderr}
    if expect_ok is not None and bool(reply.get('ok')) != expect_ok:
        raise AssertionError(f'ca browser {" ".join(args)} -> {reply}')
    return reply


# --------------------------------------------------- minimal DevTools client


class DevTools:
    """Just enough WebSocket (RFC 6455, client side) for a few CDP calls."""

    def __init__(self, url):
        parsed = urlparse(url)
        self.sock = socket.create_connection((parsed.hostname, parsed.port), timeout=15)
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        self.sock.sendall(
            f'GET {parsed.path} HTTP/1.1\r\nHost: {parsed.netloc}\r\nUpgrade: websocket\r\n'
            f'Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n'.encode()
        )
        response = b''
        while b'\r\n\r\n' not in response:
            response += self.sock.recv(4096)
        if b' 101 ' not in response.split(b'\r\n', 1)[0]:
            raise ConnectionError(response[:200])
        self.ids = 0

    def exact(self, size):
        data = b''
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise ConnectionError('closed')
            data += chunk
        return data

    def call(self, method, **params):
        self.ids += 1
        payload = json.dumps({'id': self.ids, 'method': method, 'params': params}).encode()
        mask = secrets.token_bytes(4)
        size = len(payload)
        header = bytes([0x81])
        if size < 126:
            header += bytes([0x80 | size])
        elif size < 65536:
            header += bytes([0x80 | 126]) + struct.pack('>H', size)
        else:
            header += bytes([0x80 | 127]) + struct.pack('>Q', size)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(header + mask + masked)
        while True:
            first, second = self.exact(2)
            size = second & 0x7F
            if size == 126:
                size = struct.unpack('>H', self.exact(2))[0]
            elif size == 127:
                size = struct.unpack('>Q', self.exact(8))[0]
            message = self.exact(size)
            if first & 0x0F != 1:
                continue
            reply = json.loads(message)
            if reply.get('id') == self.ids:
                if 'error' in reply:
                    raise RuntimeError(reply['error'])
                return reply.get('result', {})

    def close(self):
        self.sock.close()


def devtools_port(profile):
    path = Path(profile) / 'DevToolsActivePort'
    for _ in range(100):
        if path.is_file() and path.read_text().strip():
            return int(path.read_text().split()[0])
        time.sleep(0.1)
    raise TimeoutError('Chrome did not publish a DevTools port')


def page_target(port, predicate):
    with urlopen(f'http://127.0.0.1:{port}/json/list') as response:
        targets = json.load(response)
    for target in targets:
        if target['type'] == 'page' and predicate(target):
            return target
    raise LookupError('No matching page target')


# ------------------------------------------------------------------ helpers


def default_chrome():
    found = sorted(
        glob.glob(str(Path.home() / '.cache/ms-playwright/chromium-*/chrome-linux64/chrome'))
        + glob.glob(
            str(
                Path.home() / 'Library/Caches/ms-playwright/chromium-*/chrome-mac*/'
                'Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing'
            )
        )
    )
    return found[-1] if found else shutil.which('chromium') or shutil.which('chromium-browser')


def launch(chrome, profile, extension, env, headed=False):
    # Headed runs show windows on the display in env: point WAYLAND_DISPLAY at a private
    # desktop (ca env start) and unset DISPLAY, never at the user's own session.
    shown = ['--ozone-platform=wayland'] if env.get('WAYLAND_DISPLAY') else []
    command = [
        chrome,
        *(shown if headed else ['--headless=new']),
        f'--user-data-dir={profile}',
        # Never ask the desktop keyring (a GNOME unlock prompt would take focus).
        '--password-store=basic',
        '--use-mock-keychain',
        '--no-first-run',
        '--no-default-browser-check',
        # Loaded below as "Load unpacked" does, so reloads behave as in a user's browser.
        '--enable-unsafe-extension-debugging',
        '--remote-debugging-port=0',
        'about:blank',
    ]
    return subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)  # fmt: skip


def load_unpacked(profile, extension):
    """Turn on developer mode and load the extension unpacked, like a user would."""
    port = devtools_port(profile)
    with urlopen(f'http://127.0.0.1:{port}/json/version') as response:
        browser = DevTools(json.load(response)['webSocketDebuggerUrl'])
    with urlopen(
        Request(f'http://127.0.0.1:{port}/json/new?chrome://extensions', method='PUT')
    ) as r:
        page = json.load(r)
    time.sleep(1)
    tools = DevTools(page['webSocketDebuggerUrl'])
    tools.call('Runtime.evaluate', awaitPromise=True,
               expression='chrome.developerPrivate.updateProfileConfiguration({inDeveloperMode: true})')  # fmt: skip
    tools.close()
    with urlopen(f'http://127.0.0.1:{port}/json/close/{page["id"]}') as r:
        r.read()
    browser.call('Extensions.loadUnpacked', path=str(extension))
    browser.close()


def wait_instances(env, count, timeout=20, exclude_pid=None):
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = ca('instances', env=env).get('instances', [])
        if len(found) == count and all(i.get('pid') != exclude_pid for i in found):
            return found
        time.sleep(0.3)
    raise TimeoutError(f'Expected {count} instances, saw {found}')


def windows_snapshot(env, instance):
    info = next(
        i for i in ca('instances', env=env)['instances'] if instance in (i['instance'], i['label'])
    )
    tabs = ca('tabs', '--instance', instance, env=env)['tabs']
    return {
        'focused_windows': sorted(w['window'] for w in info['windows'] if w['focused']),
        'active_tabs': sorted(t['tab'] for t in tabs if t['active']),
    }


# -------------------------------------------------------- pointer and frames


def pen_pixels(path, colour=(10, 124, 96)):
    """Count pixels near a colour (default: the pen's green tag) in an 8-bit RGB(A) PNG."""
    data = Path(path).read_bytes()
    width, height, depth, kind = struct.unpack('>IIBB', data[16:26])
    assert depth == 8 and kind in (2, 6), (depth, kind)
    chunks, at = b'', 8
    while at < len(data):
        size, name = struct.unpack('>I4s', data[at : at + 8])
        if name == b'IDAT':
            chunks += data[at + 8 : at + 8 + size]
        at += size + 12
    raw = zlib.decompress(chunks)
    channels = 3 if kind == 2 else 4
    stride = width * channels
    previous = bytearray(stride)
    count = 0
    for row in range(height):
        start = row * (stride + 1)
        mode, line = raw[start], bytearray(raw[start + 1 : start + 1 + stride])
        for i in range(stride):
            left = line[i - channels] if i >= channels else 0
            up, corner = previous[i], previous[i - channels] if i >= channels else 0
            if mode == 1:
                line[i] = (line[i] + left) & 255
            elif mode == 2:
                line[i] = (line[i] + up) & 255
            elif mode == 3:
                line[i] = (line[i] + (left + up) // 2) & 255
            elif mode == 4:
                guess = left + up - corner
                pick = min((abs(guess - left), 0, left), (abs(guess - up), 1, up),
                           (abs(guess - corner), 2, corner))[2]  # fmt: skip
                line[i] = (line[i] + pick) & 255
        for i in range(0, stride, channels):
            r, g, b = line[i], line[i + 1], line[i + 2]
            if abs(r - colour[0]) < 12 and abs(g - colour[1]) < 12 and abs(b - colour[2]) < 12:
                count += 1
        previous = line
    return count


def check_pointer(env, url, work, port):
    """Coordinates, gestures and frames on the drawing fixture, as an editor needs them."""
    opened = ca('open', url + 'draw', '--instance', 'alpha', '--owner', 'painter', env=env,
                expect_ok=True)  # fmt: skip
    tab, lease = opened['handle'], opened['lease']

    def act(action, *rest):
        began = time.perf_counter()
        reply = ca(action, '--tab', tab, '--lease', lease, *rest, env=env)
        reply['_seconds'] = round(time.perf_counter() - began, 3)
        return reply

    def pad(selector):
        return ca('read', '--tab', tab, '--frame', '#pad', '--selector', selector, env=env)

    hint = ca('locate', '--tab', tab, '--text', 'Line tool', env=env)
    missing = act('click', '--text', 'Line tool')
    step('frame_hint', hint.get('count') == 0 and (hint.get('frames') or [{}])[0].get('frame')
         == ['#pad'] and missing.get('error') == 'element_not_found'
         and "--frame '#pad'" in missing.get('detail', ''), frames=hint.get('frames'))  # fmt: skip
    tool = act('click', '--frame', '#pad', '--text', 'Line tool')
    step('hover_gated_toolbar_in_frame', tool.get('ok') and pad('#tool').get('text') == 'line',
         reply=tool.get('error') or tool.get('target'))  # fmt: skip
    drag = act('drag', '--frame', '#pad', '--selector', 'canvas', '--from', '20', '30',
               '--via', '100', '30', '--to', '150', '120')  # fmt: skip
    strokes = pad('#strokes').get('text', '')
    step('drag_in_scrolled_scaled_frame', drag.get('ok') and 'line 20,30 150,120' in strokes
         and drag.get('moves', 0) >= 20, strokes=strokes, moves=drag.get('moves'),
         seconds=drag['_seconds'])  # fmt: skip

    act('click', '--frame', '#pad', '--text', 'Dot tool')
    canvas = ca('locate', '--tab', tab, '--frame', '#pad', '--selector', 'canvas', env=env)
    rect = canvas['elements'][0]['rect']
    viewport = act('click', '--x', str(rect['x'] + 1.5 * 50), '--y', str(rect['y'] + 1.5 * 40))
    strokes = pad('#strokes').get('text', '')
    step('viewport_point_through_scaled_frame', viewport.get('ok') and 'dot 50,40 50,40' in strokes
         and abs(rect['width'] - 450) <= 1, rect=rect, strokes=strokes)  # fmt: skip
    double = act('click', '--frame', '#pad', '--selector', 'canvas', '--x', '60', '--y', '50',
                 '--count', '2')  # fmt: skip
    right = act('click', '--frame', '#pad', '--selector', 'canvas', '--x', '70', '--y', '50',
                '--button', 'right')  # fmt: skip
    shifted = act('click', '--frame', '#pad', '--selector', 'canvas', '--x', '80', '--y', '50',
                  '--modifiers', 'shift')  # fmt: skip
    wheel = act('scroll', '--frame', '#pad', '--selector', 'canvas', '--delta', '-120')
    log = pad('#log').get('text', '')
    step('double_right_modifier_and_wheel', double.get('ok') and right.get('ok') and shifted.get('ok')
         and 'dblclick@60,50' in log and 'contextmenu' in log and 'shift' in log
         and 'wheel-120' in log and wheel.get('handled_by_page') is True, log=log)  # fmt: skip
    page = act('scroll', '--x', '5', '--y', '5', '--delta', '300')
    scrolled = page.get('scrolled') or {}
    step('scroll_page', page.get('ok') and scrolled.get('element') == 'page'
         and scrolled.get('to', 0) - scrolled.get('from', 0) == 300, scrolled=scrolled)  # fmt: skip
    # A toolbar that cancels pointerdown keeps focus in the field, as in Chrome.
    act('click', '--frame', '#pad', '--selector', '#label')
    act('click', '--frame', '#pad', '--selector', '#bold')
    kept = 'bold:label' in pad('#log').get('text', '')
    # A wheel over an exhausted panel scrolls the page instead.
    inner = act('scroll', '--selector', '#panel', '--delta', '500')
    outer = act('scroll', '--selector', '#panel', '--delta', '200')
    step('focus_kept_and_scroll_chains', kept and (inner.get('scrolled') or {}).get('element', {})
         != 'page' and (outer.get('scrolled') or {}).get('element') == 'page',
         inner=inner.get('scrolled'), outer=outer.get('scrolled'), log=pad('#log').get('text'))  # fmt: skip
    contained = [act('scroll', '--selector', '#contained', '--delta', '300') for _ in range(2)]
    off = ca('locate', '--tab', tab, '--selector', '#off', env=env)['elements'][0]['rect']
    disabled = act('click', '--x', str(off['x'] + 5), '--y', str(off['y'] + 5))
    help_link = ca('locate', '--tab', tab, '--selector', '#help', env=env)['elements'][0]['rect']
    helped = act('click', '--x', str(help_link['x'] + 3), '--y', str(help_link['y'] + 3))
    step('contained_scroll_and_disabled_point', [(c.get('scrolled') or {}).get('to') for c in
         contained] == [40, 40] and disabled.get('error') == 'element_not_interactable'
         and helped.get('ok') and ca('status', '--tab', tab, '--lease', lease, env=env)
         .get('url', '').endswith('#helped'),
         contained=[c.get('scrolled') for c in contained], disabled=disabled.get('error'),
         link=helped.get('url') or helped.get('error'))  # fmt: skip
    hover = act('move', '--frame', '#pad', '--text', 'Dot tool')
    step('move_hovers', hover.get('ok') and hover['target']['tag'] == 'span')

    # The frame from another origin sits below the fold: ca scrolls it into view first,
    # for a ref located without scrolling as well as for --frame.
    act('scroll', '--x', '5', '--y', '5', '--delta', '-5000')
    ref = ca('locate', '--tab', tab, '--frame', '#foreign', '--text', 'Press me', env=env)
    by_ref = act('click', '--ref', ref['elements'][0]['ref'])
    centred = act('scroll', '--frame', '#foreign', '--delta', '10')
    step('foreign_frame_ref_below_fold_and_scroll', by_ref.get('ok') and centred.get('ok'),
         errors=[by_ref.get('error'), centred.get('error')], point=by_ref.get('point'))  # fmt: skip
    act('scroll', '--x', '5', '--y', '5', '--delta', '-5000')
    foreign = act('click', '--frame', '#foreign', '--selector', 'button')
    pressed = ca('read', '--tab', tab, '--frame', '#foreign', '--selector', 'button', env=env)
    foreign_point = act('click', '--selector', '#foreign', '--x', '20', '--y', '20')
    outside = act('click', '--x', '5000', '--y', '5')
    no_target = act('click')
    old_style = act('click', '--frame', '#pad', '--text', 'Line tool')
    step('foreign_frame_and_refusals', foreign.get('ok') and pressed.get('text') == 'Pressed'
         and foreign_point.get('ok') and foreign_point['target']['ref'].count('@') == 1
         and outside.get('error') == 'point_outside_viewport'
         and no_target.get('error') == 'invalid_target' and old_style.get('ok'),
         errors=[r.get('error') for r in (foreign, foreign_point, outside, no_target)],
         pressed=pressed.get('text'))  # fmt: skip

    # The human watches, then takes over in the middle of a slow drag.
    target = page_target(port, lambda t: t['url'].startswith(url + 'draw'))
    with urlopen(f'http://127.0.0.1:{port}/json/activate/{target["id"]}') as response:
        response.read()
    # A visible click leaves the pen resting on the canvas. The pen is never hidden from
    # the user, so a capture of the shown tab includes it and says where it is.
    act('click', '--frame', '#pad', '--selector', 'canvas', '--x', '150', '--y', '100')
    shot = ca('screenshot', '--tab', tab, str(work / 'draw.png'), env=env)
    teal = pen_pixels(work / 'draw.png') if shot.get('ok') else None
    step('shown_screenshot_includes_pen', shot.get('ok') and shot.get('scale', 0) > 0
         and shot.get('viewport', {}).get('width') and teal and shot.get('pen') == 'included'
         and shot.get('pen_rect'), scale=shot.get('scale'),
         viewport=shot.get('viewport'), pen_pixels=teal, bytes=(work / 'draw.png').stat().st_size
         if shot.get('ok') else None)  # fmt: skip
    before = pad('#strokes').get('text', '')
    pending = {}
    slow = threading.Thread(target=lambda: pending.setdefault('reply', act(
        'drag', '--frame', '#pad', '--selector', 'canvas', '--from', '10', '10', '--to', '290',
        '190', '--interval', '0.05')))  # fmt: skip
    slow.start()
    time.sleep(1.2)
    human = DevTools(target['webSocketDebuggerUrl'])
    for kind in ('mousePressed', 'mouseReleased'):
        human.call('Input.dispatchMouseEvent', type=kind, x=5, y=5, button='left', clickCount=1)
    human.close()
    slow.join(30)
    taken = pending.get('reply', {})
    after = pad('#strokes').get('text', '')
    held = ca('status', '--tab', tab, '--lease', lease, env=env)
    step('human_input_cancels_drag_and_keeps_lease', taken.get('error') == 'human_active'
         and taken.get('gesture') == 'cancelled' and after == before
         and 'cancel' in pad('#log').get('text', '') and held.get('holds_lease')
         and held.get('human_active') and held.get('human_input') == 'pointerdown',
         reply={k: taken.get(k) for k in ('error', 'gesture', 'moved', 'retry_after_ms')},
         status={k: held.get(k) for k in ('holds_lease', 'human_active', 'human_input')})  # fmt: skip
    # Meanwhile the pen shows the pause: its tag turns grey instead of green.
    shot = ca('screenshot', '--tab', tab, str(work / 'paused.png'), env=env)
    grey = pen_pixels(work / 'paused.png', (91, 107, 103)) if shot.get('ok') else 0
    green = pen_pixels(work / 'paused.png') if shot.get('ok') else None
    step('pen_shows_pause', shot.get('pen') == 'included' and grey > 100 and green < grey,
         grey_pixels=grey, green_pixels=green)  # fmt: skip
    # The next action waits for the human's pause, then runs on the same lease.
    resumed = act('click', '--frame', '#pad', '--text', 'Dot tool')
    step('action_waits_for_human_pause', resumed.get('ok')
         and resumed.get('human_waited_ms', 0) > 0 and resumed['_seconds'] < 10,
         waited_ms=resumed.get('human_waited_ms'), seconds=resumed['_seconds'],
         error=resumed.get('error'))  # fmt: skip
    shot = ca('screenshot', '--tab', tab, str(work / 'resumed.png'), env=env)
    back = pen_pixels(work / 'resumed.png') if shot.get('ok') else 0
    step('pen_resumes_after_pause', back > 300, green_pixels=back)
    # A human who keeps working outlasts a short request: refused, nothing done, lease kept.
    busy = {'on': True}

    def keep_typing():
        typist = DevTools(target['webSocketDebuggerUrl'])
        while busy['on']:
            typist.call('Input.dispatchMouseEvent', type='mouseWheel', x=5, y=5, deltaX=0, deltaY=1)
            time.sleep(0.3)
        typist.close()

    typing = threading.Thread(target=keep_typing)
    typing.start()
    time.sleep(0.5)
    waited = ca('click', '--tab', tab, '--lease', lease, '--frame', '#pad', '--text', 'Line tool',
                '--timeout', '6', env=env)  # fmt: skip
    busy['on'] = False
    typing.join(10)
    tool = pad('#tool').get('text')
    step('busy_human_refuses_with_lease_kept', waited.get('error') == 'human_active'
         and waited.get('performed') is False and waited.get('retry_after_ms', 0) > 0
         and waited.get('human_waited_ms', 0) >= 2000 and tool == 'dot'
         and ca('status', '--tab', tab, '--lease', lease, env=env).get('holds_lease'),
         reply={k: waited.get(k) for k in ('error', 'performed', 'retry_after_ms',
                                           'human_waited_ms', 'human_input')}, tool=tool)  # fmt: skip

    # Browser pages are listed as unscriptable and refused with the reason.
    request = Request(f'http://127.0.0.1:{port}/json/new?chrome://version', method='PUT')
    with urlopen(request) as response:
        internal = json.load(response)
    time.sleep(0.5)
    tabs = ca('tabs', '--instance', 'alpha', env=env)['tabs']
    listed = next((t for t in tabs if str(t.get('url', '')).startswith('chrome://version')), {})
    refused = ca('claim', '--tab', listed.get('handle', ''), env=env)
    step('browser_page_marked_and_refused', listed.get('scriptable') is False
         and listed.get('restriction') == 'browser_page'
         and refused.get('error') == 'page_unavailable'
         and refused.get('restriction') == 'browser_page', detail=refused.get('detail'))  # fmt: skip
    with urlopen(f'http://127.0.0.1:{port}/json/close/{internal["id"]}') as response:
        response.read()

    timings = {}
    for name, command in (
        (
            'read',
            lambda: ca('read', '--tab', tab, '--frame', '#pad', '--selector', '#tool', env=env),
        ),
        ('click', lambda: act('click', '--frame', '#pad', '--text', 'Dot tool')),
        (
            'point_click',
            lambda: act(
                'click', '--frame', '#pad', '--selector', 'canvas', '--x', '30', '--y', '30'
            ),
        ),
        ('tabs', lambda: ca('tabs', '--instance', 'alpha', env=env)),
    ):
        samples = []
        for _ in range(5):
            began = time.perf_counter()
            command()
            samples.append(time.perf_counter() - began)
        timings[name] = round(sorted(samples)[2], 3)
    step('pointer_timings', True, median_seconds=timings, drag_seconds=drag['_seconds'])
    ca('close', '--tab', tab, '--lease', lease, env=env)


def check_cross_origin_editor(env, url):
    """An editor in a frame from another origin, embedded the way SharePoint embeds Word."""
    opened = ca('open', url + 'host', '--instance', 'alpha', '--owner', 'editor', env=env,
                expect_ok=True)  # fmt: skip
    tab, lease = opened['handle'], opened['lease']
    frame = 'iframe[name="editor_frame"]'

    def act(action, *rest):
        return ca(action, '--tab', tab, '--lease', lease, *rest, env=env)

    def doc():
        return ca('read', '--tab', tab, '--frame', frame, '--selector', '#doc', env=env)

    ca('wait', '--tab', tab, '--frame', frame, '--selector', '#doc', env=env, expect_ok=True)
    top = ca('read', '--tab', tab, env=env)
    listed = (top.get('frames') or [{}])[0]
    text = doc()
    step('editor_frame_listed_and_read', top.get('ok') and listed.get('frame') == frame
         and listed.get('same_origin') is False and listed['rect']['width'] > 0
         and 'Ths is a concept' in text.get('text', '') and '/editor' in text.get('url', ''),
         frames=top.get('frames'), text=text.get('text'), url=text.get('url'))  # fmt: skip

    hint = ca('locate', '--tab', tab, '--text', 'Replace all', env=env)
    nested = act('click', '--text', 'Press me')
    step('foreign_frame_hints', hint.get('count') == 0
         and (hint.get('frames') or [{}])[0].get('frame') == [frame]
         and nested.get('error') == 'element_not_found'
         and f"--frame '{frame}' --frame '#inner'" in nested.get('detail', ''),
         frames=hint.get('frames'), detail=nested.get('detail'))  # fmt: skip

    # Find and replace through the editor's own controls, like a spelling fix.
    act('type', '--frame', frame, '--selector', '#find', 'speling')
    act('type', '--frame', frame, '--selector', '#replace', 'spelling')
    replaced = act('click', '--frame', frame, '--text', 'Replace all')
    typed = act('type', '--frame', frame, '--selector', '#doc', ' Added.')
    after = doc().get('text', '')
    step('type_and_click_in_foreign_frame', replaced.get('ok') and typed.get('ok')
         and 'with a spelling mistake. Added.' in after.replace('\n', ' '),
         text=after, errors=[replaced.get('error'), typed.get('error')])  # fmt: skip

    # Refs name their frame, so they work without --frame; points map through it.
    mark = ca('locate', '--tab', tab, '--frame', frame, '--text', 'Mark', env=env)
    element = mark['elements'][0]
    by_ref = act('click', '--ref', element['ref'])
    act('click', '--frame', frame, '--selector', '#find')
    rect = ca('locate', '--tab', tab, '--frame', frame, '--selector', '#find', env=env)
    box = rect['elements'][0]['rect']
    point = act('click', '--x', str(box['x'] + 5), '--y', str(box['y'] + box['height'] / 2))
    marked = ca('read', '--tab', tab, '--frame', frame, '--selector', '#mark', env=env)
    step('ref_and_point_in_foreign_frame', by_ref.get('ok') and marked.get('text') == 'Marked'
         and '@' in element['ref'] and point.get('ok') and point['target'].get('tag') == 'input'
         and abs(element['rect']['y'] - 8) < 20, ref=element['ref'], rect=element['rect'],
         point=point.get('target') or point.get('error'))  # fmt: skip

    # A key with no target goes to the focused field inside the frame, without --frame.
    focused = act('type', '--ref', point['target']['ref'], 'X')
    pressed = act('press', 'Backspace')
    typed_into = ca('locate', '--tab', tab, '--frame', frame, '--selector', '#find', env=env)
    keys = ca('read', '--tab', tab, '--frame', frame, '--selector', '#keys', env=env)
    step('focused_element_in_foreign_frame', focused.get('ok') and pressed.get('effect') == 'edited'
         and typed_into['elements'][0].get('value') == 'speling'
         and 'X:synthetic Backspace:synthetic' in keys.get('text', ''),
         value=typed_into['elements'][0].get('value'), keys=keys.get('text'),
         errors=[focused.get('error'), pressed.get('error')])  # fmt: skip

    inner = act('click', '--frame', frame, '--frame', '#inner', '--text', 'Press me')
    deep = ca('read', '--tab', tab, '--frame', frame, '--frame', '#inner', '--selector', 'button',
              env=env)  # fmt: skip
    step(
        'frame_inside_foreign_frame',
        inner.get('ok') and deep.get('text') == 'Pressed',
        text=deep.get('text'),
        error=inner.get('error'),
    )
    ca('close', '--tab', tab, '--lease', lease, env=env)


def check_background_work(env, url, work, port, ids):
    """Background capture, the agent window, cleanup and claims across an extension reload."""
    before = windows_snapshot(env, 'alpha')
    opened = ca('open', url + 'draw', '--instance', 'alpha', '--owner', 'viewer', env=env,
                expect_ok=True)  # fmt: skip
    tab, lease = opened['handle'], opened['lease']
    ca('wait', '--tab', tab, '--frame', '#pad', '--selector', 'canvas', env=env, expect_ok=True)
    ca('move', '--tab', tab, '--lease', lease, '--frame', '#pad', '--selector', 'canvas', env=env)
    began = time.perf_counter()
    shot = ca('screenshot', '--tab', tab, str(work / 'background.png'), env=env)
    seconds = round(time.perf_counter() - began, 3)
    magenta = pen_pixels(work / 'background.png', (255, 0, 170)) if shot.get('ok') else 0
    teal = pen_pixels(work / 'background.png') if shot.get('ok') else None
    step('background_capture_shows_iframe_canvas', shot.get('ok') and shot.get('method') == 'debugger'
         and shot.get('pen') == 'excluded' and teal == 0 and shot.get('debugger_attached')
         and magenta > 500 and windows_snapshot(env, 'alpha') == before, magenta_pixels=magenta,
         seconds=seconds, scale=shot.get('scale'), bytes=(work / 'background.png').stat().st_size
         if shot.get('ok') else None)  # fmt: skip

    # Coordinates measured on another viewport size are refused, not drawn.
    size = shot.get('viewport') or {}
    stale = ca('drag', '--tab', tab, '--lease', lease, '--frame', '#pad', '--selector', 'canvas',
               '--from', '10', '10', '--to', '40', '40',
               '--viewport', f"{size.get('width')}x{size.get('height', 0) + 56}", env=env)  # fmt: skip
    step('stale_viewport_refused', stale.get('error') == 'viewport_changed'
         and stale.get('viewport') == {'width': size.get('width'), 'height': size.get('height')},
         error=stale.get('error'), viewport=stale.get('viewport'))  # fmt: skip

    # Two agents capture their own background tabs at the same time.
    other = ca('open', url + 'draw', '--instance', 'alpha', '--owner', 'viewer-2', env=env,
               expect_ok=True)  # fmt: skip
    ca('wait', '--tab', other['handle'], '--frame', '#pad', '--selector', 'canvas', env=env)
    shots = {}
    threads = [threading.Thread(target=lambda h=h, n=n: shots.setdefault(n, ca(
        'screenshot', '--tab', h, str(work / f'parallel-{n}.png'), env=env)))
        for n, h in enumerate([tab, other['handle'], tab])]  # fmt: skip
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    sessions = ca('instances', env=env)['instances']
    held = [i.get('debugger_sessions') for i in sessions if i.get('label') == 'alpha']
    step('concurrent_background_captures', all(r.get('ok') for r in shots.values())
         and held and held[0] >= 2, errors=[r.get('error') for r in shots.values()], sessions=held)  # fmt: skip

    # The human works in their own tab while the agent drags: only input in a tab ends its claim.
    user_tab = next(t for t in ca('tabs', '--instance', 'alpha', env=env)['tabs'] if t['active'])
    user_target = page_target(port, lambda t: t['url'] == 'about:blank')
    pending = {}
    dragging = threading.Thread(target=lambda: pending.setdefault('reply', ca(
        'drag', '--tab', tab, '--lease', lease, '--frame', '#pad', '--selector', 'canvas',
        '--from', '10', '10', '--to', '200', '150', '--interval', '0.03', env=env)))  # fmt: skip
    dragging.start()
    time.sleep(0.4)
    human = DevTools(user_target['webSocketDebuggerUrl'])
    for kind in ('mousePressed', 'mouseReleased'):
        human.call('Input.dispatchMouseEvent', type=kind, x=5, y=5, button='left', clickCount=1)
    human.close()
    dragging.join(30)
    status = ca('status', '--tab', tab, '--lease', lease, env=env)
    step('unrelated_human_input_keeps_agent_claim', pending.get('reply', {}).get('ok')
         and status.get('holds_lease'), drag=pending.get('reply', {}).get('error'),
         status=status.get('stop_reason'))  # fmt: skip

    # The agents' own window: created unfocused, its tab renders as visible.
    windowed = ca('open', url + 'pad', '--instance', 'alpha', '--owner', 'viewer',
                  '--agent-window', env=env)  # fmt: skip
    second = ca('open', url + 'other', '--instance', 'alpha', '--owner', 'viewer',
                '--agent-window', env=env)  # fmt: skip
    after = windows_snapshot(env, 'alpha')
    shown = ca('screenshot', '--tab', second.get('handle', ''), str(work / 'window.png'), env=env)
    step('agent_window_unfocused_and_reused', windowed.get('ok') and second.get('ok')
         and windowed.get('window') == second.get('window') != user_tab['window']
         and after['focused_windows'] == before['focused_windows'] and second.get('active')
         and user_tab['tab'] in after['active_tabs'] and shown.get('method') == 'visible_tab',
         window=windowed.get('window'), focus=after, method=shown.get('method'),
         focus_returned=windowed.get('focus_returned'))  # fmt: skip

    # Rapid captures of a shown tab are spaced to stay within Chrome's captureVisibleTab
    # quota (about two a second) instead of attaching the debugger. The tab shows the
    # pen (a click at rest leaves it), and every capture of it says so.
    ca('click', '--tab', second.get('handle', ''), '--lease', second.get('lease', ''), '--text',
       'Press me', env=env)  # fmt: skip
    burst = {}
    takers = [threading.Thread(target=lambda n=n: burst.setdefault(n, ca(
        'screenshot', '--tab', second.get('handle', ''), str(work / f'burst-{n}.png'), env=env)))
        for n in range(4)]  # fmt: skip
    for thread in takers:
        thread.start()
    for thread in takers:
        thread.join()
    methods = sorted(r.get('method') or r.get('detail', r.get('error')) for r in burst.values())
    pens = [burst[n].get('pen') for n in range(4)]
    step('rapid_visible_captures_are_spaced', all(r.get('ok') for r in burst.values())
         and methods == ['visible_tab'] * 4 and pens == ['included'] * 4
         and not any(r.get('debugger_attached') for r in burst.values()),
         methods=methods, pens=pens)  # fmt: skip

    # An agent that lost its leases cleans up only its own tabs.
    cleaned = ca('cleanup', '--owner', 'viewer-2', env=env)
    gone = ca('status', '--tab', other['handle'], env=env)
    mine = ca('status', '--tab', tab, '--lease', lease, env=env)
    after_cleanup = [i.get('debugger_sessions') for i in ca('instances', env=env)['instances']
                     if i.get('label') == 'alpha']  # fmt: skip
    step('cleanup_closes_only_own_tabs', cleaned.get('ok') and not gone.get('exists')
         and mine.get('holds_lease') and mine.get('debugger_attached')
         and after_cleanup and after_cleanup[0] == held[0] - 1, cleaned=cleaned.get('tabs'),
         sessions=[held, after_cleanup])  # fmt: skip

    # Releasing a tab ca opened hands it to the user: it stays open, ungrouped and
    # without a claim or debugger session, and ca can no longer close it.
    given = ca('open', url + 'other', '--instance', 'alpha', '--owner', 'giver', env=env,
               expect_ok=True)  # fmt: skip
    ca('screenshot', '--tab', given['handle'], str(work / 'given.png'), env=env)
    ca('release', '--tab', given['handle'], '--lease', given['lease'], env=env, expect_ok=True)
    kept = next((t for t in ca('tabs', '--instance', 'alpha', env=env)['tabs']
                 if t['handle'] == given['handle']), {})  # fmt: skip
    status = ca('status', '--tab', given['handle'], env=env)
    step('release_hands_tab_over', kept and kept.get('claim') is None and kept.get('group') is None
         and kept.get('opened_by') == 'giver' and not status.get('debugger_attached'),
         group=kept.get('group'), opened_by=kept.get('opened_by'))  # fmt: skip
    # ca remembers that it opened the tab: a later claim of it may close it.
    again = ca('claim', '--tab', given['handle'], '--owner', 'giver', env=env)
    closed = ca('close', '--tab', given['handle'], '--lease', again.get('lease', ''), env=env)
    gone = ca('status', '--tab', given['handle'], env=env)
    step('reclaimed_agent_tab_closes', again.get('ok') and closed.get('closed') is True
         and not gone.get('exists'), close=closed.get('error'))  # fmt: skip
    # So does cleanup, for a tab its owner opened, released and claimed again.
    lent = ca('open', url + 'other', '--instance', 'alpha', '--owner', 'lender', env=env,
              expect_ok=True)  # fmt: skip
    ca('release', '--tab', lent['handle'], '--lease', lent['lease'], env=env, expect_ok=True)
    ca('claim', '--tab', lent['handle'], '--owner', 'lender', env=env, expect_ok=True)
    swept = ca('cleanup', '--owner', 'lender', env=env)
    step('cleanup_closes_reclaimed_agent_tab', swept.get('ok')
         and [t.get('result') for t in swept.get('tabs', [])] == ['close']
         and not ca('status', '--tab', lent['handle'], env=env).get('exists'),
         tabs=swept.get('tabs'))  # fmt: skip

    # Simultaneous first opens in another browser share one new agent window.
    pair = {}
    openers = [threading.Thread(target=lambda n=n: pair.setdefault(n, ca(
        'open', url + 'other', '--instance', 'beta', '--owner', 'pair', '--agent-window', env=env)))
        for n in range(2)]  # fmt: skip
    for thread in openers:
        thread.start()
    for thread in openers:
        thread.join()
    step('simultaneous_agent_window_opens_share_one', all(r.get('ok') for r in pair.values())
         and pair[0].get('window') == pair[1].get('window'),
         windows=[r.get('window') for r in pair.values()])  # fmt: skip
    ca('cleanup', '--owner', 'pair', env=env)
    # A tab opened through a redirect keeps its claim across the reload below.
    redirected = ca('open', url + 'redirect', '--instance', 'alpha', '--owner', 'viewer', env=env)

    # Reload the extension as the card's button does; claims and the human watch survive.
    build = next(i for i in ca('instances', env=env)['instances'] if i['label'] == 'alpha').get(
        'build'
    )
    host = next(i for i in ca('instances', env=env)['instances'] if i['label'] == 'alpha')['pid']
    with urlopen(
        Request(f'http://127.0.0.1:{port}/json/new?chrome://extensions', method='PUT')
    ) as r:
        page = json.load(r)
    time.sleep(1)
    tools = DevTools(page['webSocketDebuggerUrl'])
    extension = browser_commands_id()
    tools.call('Runtime.evaluate', expression=f'chrome.developerPrivate.reload({json.dumps(extension)}, '
               '{failQuietly: true})', awaitPromise=True)  # fmt: skip
    tools.close()
    with urlopen(f'http://127.0.0.1:{port}/json/close/{page["id"]}') as r:
        r.read()
    back = wait_instances(env, 2, timeout=40, exclude_pid=host)
    # ca's own reload, unneeded with the build already loaded, is a no-op.
    same = ca('reload', '--instance', 'alpha', env=env)
    reloaded = next(i for i in back if i['label'] == 'alpha')
    kept = ca('status', '--tab', tab, '--lease', lease, env=env)
    acted = ca(
        'click', '--tab', tab, '--lease', lease, '--frame', '#pad', '--text', 'Dot tool', env=env
    )
    after_redirect = ca('status', '--tab', redirected.get('handle', ''), '--lease',
                        redirected.get('lease', ''), env=env)  # fmt: skip
    step('claims_survive_extension_reload', reloaded.get('build') == build and kept.get('holds_lease')
         and acted.get('ok') and same.get('reloaded') is False
         and after_redirect.get('holds_lease'), build=build, redirect=after_redirect.get('url'),
         kept=kept.get('holds_lease'), acted=acted.get('error'), noop=same)  # fmt: skip
    # A trusted click in the agent's tab after the reload still pauses that claim.
    target = page_target(port, lambda t: t['url'].startswith(url + 'draw'))
    human = DevTools(target['webSocketDebuggerUrl'])
    for kind in ('mousePressed', 'mouseReleased'):
        human.call('Input.dispatchMouseEvent', type=kind, x=5, y=5, button='left', clickCount=1)
    human.close()
    time.sleep(0.6)
    paused = ca('status', '--tab', tab, '--lease', lease, env=env)
    step('human_watch_live_after_reload', paused.get('holds_lease') and paused.get('human_active'),
         status={k: paused.get(k) for k in ('holds_lease', 'human_active', 'stop_reason')})  # fmt: skip
    for handle, held in ((windowed.get('handle'), windowed.get('lease')),
                         (second.get('handle'), second.get('lease'))):  # fmt: skip
        if handle:
            ca('close', '--tab', handle, '--lease', held, env=env)


def check_agent_window_beside_other_app(env, url, port):
    """Headed: with focus in another application (beta), a new agent window leaves it there."""
    user_page = page_target(port, lambda t: t['type'] == 'page')
    tools = DevTools(user_page['webSocketDebuggerUrl'])
    tools.call('Page.bringToFront')
    tools.close()
    time.sleep(1)
    before = {name: windows_snapshot(env, name)['focused_windows'] for name in ('alpha', 'beta')}
    opened = ca('open', url + 'pad', '--instance', 'alpha', '--owner', 'beside', '--agent-window',
                env=env)  # fmt: skip
    after = {name: windows_snapshot(env, name)['focused_windows'] for name in ('alpha', 'beta')}
    state = next((w['state'] for i in ca('instances', env=env)['instances'] if i['label'] == 'alpha'
                  for w in i['windows'] if w['window'] == opened.get('window')), None)  # fmt: skip
    # A compositor that honours Chrome's request never focuses it; KWin does, and the
    # window is minimized so that focus returns to the other application.
    step('agent_window_leaves_other_app_focused', opened.get('ok') and not before['alpha']
         and before['beta'] and after == before
         and (opened.get('focus_returned'), state) in ((None, 'normal'), ('minimized', 'minimized')),
         before=before, after=after, focus_returned=opened.get('focus_returned'), state=state)  # fmt: skip
    if opened.get('ok'):
        ca('close', '--tab', opened['handle'], '--lease', opened['lease'], env=env)


def browser_commands_id():
    sys.path.insert(0, str(ROOT))
    from computer_artist import browser_commands

    return browser_commands.extension_id(browser_commands.bundled_manifest())


# --------------------------------------------------------------------- main


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--chrome', default=os.environ.get('CA_TEST_CHROME') or default_chrome())
    parser.add_argument(
        '--idle', type=float, default=40, help='Idle seconds for the keepalive check'
    )
    parser.add_argument('--evidence', type=Path, help='Write the JSON results here')
    parser.add_argument('--headed', action='store_true',
                        help="Show windows on env's display, such as a private KWin desktop")  # fmt: skip
    parser.add_argument('--keep', action='store_true', help='Keep temporary folders')
    args = parser.parse_args()
    if not args.chrome or not Path(args.chrome).is_file():
        print('Chrome for Testing or Chromium not found; pass --chrome PATH', file=sys.stderr)
        return 2
    work = Path(tempfile.mkdtemp(prefix='ca-browser-check-'))
    # macOS limits Unix socket paths to 104 bytes, which its per-user temporary folder
    # plus these folder names exceed; the host's socket then never binds.
    short = '/tmp' if sys.platform == 'darwin' else work
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR') or short)
    rendezvous = Path(tempfile.mkdtemp(prefix='ca-bt-', dir=runtime))
    env = {
        **os.environ,
        'CA_BROWSER_DATA_DIR': str(work / 'data'),
        'CA_BROWSER_DIR': str(rendezvous),
        'XDG_CONFIG_HOME': str(work / 'config'),
    }
    store = work / 'notes.json'
    server = serve(store)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{server.server_address[1]}/'
    browsers = {}
    try:
        profiles = {name: work / f'profile-{name}' for name in ('alpha', 'beta')}
        installed = ca('install', *[f'--user-data-dir={p}' for p in profiles.values()],
                       env=env, expect_ok=True)  # fmt: skip
        step('install_scoped', [h['browser'].startswith('user-data-dir:') for h in installed['hosts']]
             == [True, True], hosts=len(installed['hosts']))  # fmt: skip
        extension = installed['extension_dir']
        version = subprocess.run([args.chrome, '--version'], capture_output=True, text=True)
        step('browser', True, version=version.stdout.strip(), platform=sys.platform)
        ids = {}
        for count, (name, profile) in enumerate(profiles.items(), 1):
            # Start them one at a time so each new instance gets its profile's label.
            browsers[name] = launch(args.chrome, profile, extension, env, args.headed)
            load_unpacked(profile, extension)
            found = wait_instances(env, count)
            ids[name] = next(i['instance'] for i in found if i['instance'] not in ids.values())
            ca('label', name, '--instance', ids[name], env=env, expect_ok=True)
        listed = {i['label']: i for i in ca('instances', env=env)['instances']}
        step('two_instances_labelled', set(listed) == {'alpha', 'beta'},
             instances={k: v['instance'] for k, v in listed.items()})  # fmt: skip
        ambiguous = ca('tabs', env=env)
        step('ambiguous_instance_refused', ambiguous.get('error') == 'ambiguous_instance')

        before = windows_snapshot(env, 'alpha')
        # Two independent agents, one per browser instance, at the same time.
        replies = {}

        def agent_alpha():
            opened = ca(
                'open', url, '--instance', 'alpha', '--owner', 'agent-a', env=env, expect_ok=True
            )
            tab, lease = opened['handle'], opened['lease']
            fields = ca('locate', '--tab', tab, '--role', 'textbox', env=env, expect_ok=True)
            ca('type', '--tab', tab, '--lease', lease, '--ref', fields['elements'][0]['ref'],
               'Bridge note', env=env, expect_ok=True)  # fmt: skip
            ca('type', '--tab', tab, '--lease', lease, '--selector', '#body', 'typed by ca',
               env=env, expect_ok=True)  # fmt: skip
            ca(
                'click',
                '--tab',
                tab,
                '--lease',
                lease,
                '--text',
                'Add note',
                env=env,
                expect_ok=True,
            )
            ca('wait', '--tab', tab, '--text', 'Ready for review', env=env, expect_ok=True)
            replies['alpha'] = {**opened, 'fields': fields}

        def agent_beta():
            opened = ca('open', url + 'other', '--instance', 'beta', '--owner', 'agent-b', env=env,
                        expect_ok=True)  # fmt: skip
            ca('click', '--tab', opened['handle'], '--lease', opened['lease'], '--text', 'Press me',
               env=env, expect_ok=True)  # fmt: skip
            replies['beta'] = {**opened, 'read': ca('read', '--tab', opened['handle'], env=env)}

        threads = [threading.Thread(target=agent_alpha), threading.Thread(target=agent_beta)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        alpha, beta = replies['alpha'], replies['beta']
        step('background_tabs_opened', not alpha['active'] and not beta['active'],
             alpha=alpha['handle'], beta=beta['handle'])  # fmt: skip
        step('locate_returns_refs', alpha['fields']['count'] == 2,
             names=[e['name'] for e in alpha['fields']['elements']])  # fmt: skip
        step('independent_instance_click', 'Pressed' in beta['read']['text'])
        stored = json.loads(store.read_text())
        step('form_submitted_to_server', stored == [{'title': 'Bridge note', 'body': 'typed by ca'}],
             stored=stored)  # fmt: skip
        tab, lease = alpha['handle'], alpha['lease']
        reloaded = ca(
            'navigate', '--tab', tab, '--lease', lease, '--reload', env=env, expect_ok=True
        )
        text = ca('read', '--tab', tab, '--selector', '#notes', env=env, expect_ok=True)['text']
        step('visible_after_reload', reloaded['loaded'] and 'Bridge note: typed by ca' in text,
             text=text)  # fmt: skip
        after = windows_snapshot(env, 'alpha')
        step('no_tab_or_window_focus_change', before == after, before=before, after=after)

        options = ca('locate', '--tab', tab, '--selector', '#colour', env=env, expect_ok=True)[
            'elements'
        ][0]
        selected = ca(
            'select', '--tab', tab, '--lease', lease, '--selector', '#colour', 'blue', env=env
        )
        unchanged = ca(
            'select', '--tab', tab, '--lease', lease, '--selector', '#colour', 'blue', env=env
        )
        after_select = ca('locate', '--tab', tab, '--selector', '#colour', env=env, expect_ok=True)[
            'elements'
        ][0]
        events = ca('read', '--tab', tab, '--selector', '#select-events', env=env, expect_ok=True)[
            'text'
        ]
        step(
            'select_readback_and_events',
            options['value'] == 'red'
            and len(options['options']) == 6
            and selected.get('changed') is True
            and unchanged.get('changed') is False
            and after_select['value'] == 'blue'
            and after_select['options'][1]['selected']
            and after_select['options'][2]['disabled']
            and after_select['options'][3]['disabled']
            and events == 'input,change',
            events=events,
            options=after_select['options'],
        )
        for selector, value, error in [
            ('#colour', 'missing', 'option_not_found'),
            ('#colour', 'disabled', 'element_not_interactable'),
            ('#colour', 'grouped', 'element_not_interactable'),
            ('#colour', 'duplicate', 'ambiguous_target'),
            ('#disabled-select', 'x', 'element_not_interactable'),
            ('#multiple', 'x', 'unsupported_control'),
            ('#title', 'x', 'unsupported_control'),
        ]:
            result = ca(
                'select', '--tab', tab, '--lease', lease, '--selector', selector, value, env=env
            )
            step(
                'select_refuses_' + selector[1:] + '_' + value,
                result.get('error') == error,
                expected=error,
                actual=result.get('error'),
            )

        stale = ca('click', '--tab', tab, '--lease', lease, '--ref',
                   alpha['fields']['elements'][0]['ref'], env=env)  # fmt: skip
        step('stale_element_refused', stale.get('error') == 'stale_element')
        wrong = ca('click', '--tab', tab, '--lease', 'feedfacefeedfacefeedface', '--text', 'Add note',
                   env=env)  # fmt: skip
        step(
            'wrong_lease_refused', wrong.get('error') == 'lease_mismatch', owner=wrong.get('owner')
        )
        again = ca('claim', '--tab', tab, env=env)
        step('double_claim_refused', again.get('error') == 'tab_claimed')
        other = ca('read', '--tab', beta['handle'], '--instance', 'alpha', env=env)
        step('wrong_instance_refused', other.get('error') == 'wrong_instance')
        missing = ca('read', '--tab', f'{ids["alpha"]}:999999', env=env)
        step('missing_tab_refused', missing.get('error') == 'target_unavailable')
        hidden = ca('screenshot', '--tab', tab, str(work / 'hidden.png'), env=env)
        still = windows_snapshot(env, 'alpha')
        step('background_screenshot_renders_without_switching', hidden.get('ok')
             and hidden.get('method') == 'debugger' and hidden.get('width', 0) > 0
             and still == before, method=hidden.get('method'), error=hidden.get('error'))  # fmt: skip
        unleased = ca('focus', '--tab', tab, '--lease', 'wrong', env=env)
        focused = ca('focus', '--tab', tab, '--lease', lease, env=env)
        held = ca('status', '--tab', tab, '--lease', lease, env=env)
        shown = ca('screenshot', '--tab', tab, str(work / 'focused.png'), env=env)
        step('focus_requires_lease_and_shows_tab', unleased.get('ok') is False
             and focused.get('focused') and focused.get('active') and held.get('holds_lease')
             and shown.get('ok'), unleased=unleased.get('error'), focused=focused.get('active'))  # fmt: skip

        # A page the "user" already has open in the active tab: claim it, capture it.
        port = devtools_port(profiles['beta'])
        user_page = page_target(port, lambda t: t['url'] == 'about:blank')
        tools = DevTools(user_page['webSocketDebuggerUrl'])
        tools.call('Page.navigate', url=url)
        time.sleep(1.5)
        tabs = ca('tabs', '--instance', 'beta', env=env)['tabs']
        existing = next(t for t in tabs if t['active'])
        claimed = ca(
            'claim', '--tab', existing['handle'], '--owner', 'agent-b', env=env, expect_ok=True
        )
        shot = ca('screenshot', '--tab', existing['handle'], str(work / 'visible.png'), env=env)
        step('existing_tab_claim_and_screenshot', shot.get('ok') and shot['width'] > 0,
             size=[shot.get('width'), shot.get('height')], url=claimed['url'])  # fmt: skip
        # The human clicks inside the page's iframe: the agent pauses, the lease holds.
        frame = tools.call('Runtime.evaluate', returnByValue=True, expression=(
            'JSON.stringify(document.getElementById("frame").getBoundingClientRect())'
        ))['result']['value']  # fmt: skip
        box = json.loads(frame)
        x, y = box['x'] + 20, box['y'] + 20
        for kind in ('mousePressed', 'mouseReleased'):
            tools.call('Input.dispatchMouseEvent', type=kind, x=x, y=y, button='left', clickCount=1)
        time.sleep(0.5)
        status = ca('status', '--tab', existing['handle'], '--lease', claimed['lease'], env=env)
        hovered = ca('move', '--tab', existing['handle'], '--lease', claimed['lease'], '--text',
                     'Add note', env=env)  # fmt: skip
        step('human_input_in_iframe_pauses', status.get('holds_lease') and status.get('human_active')
             and hovered.get('ok') and hovered.get('human_waited_ms', 0) > 0,
             status={k: status.get(k) for k in ('holds_lease', 'human_active', 'human_input')},
             waited_ms=hovered.get('human_waited_ms'), error=hovered.get('error'))  # fmt: skip
        # The human types in the top document.
        tools.call('Input.dispatchKeyEvent', type='keyDown', key='a', text='a')
        tools.call('Input.dispatchKeyEvent', type='keyUp', key='a')
        tools.close()
        time.sleep(0.5)
        status = ca('status', '--tab', existing['handle'], '--lease', claimed['lease'], env=env)
        step('human_key_pauses', status.get('holds_lease') and status.get('human_active')
             and status.get('human_input') == 'keydown', status=status.get('human_input'))  # fmt: skip
        # ca never closes a tab the user opened, even while it holds the claim.
        refused = ca('close', '--tab', existing['handle'], '--lease', claimed['lease'], env=env)
        released = ca('release', '--tab', existing['handle'], '--lease', claimed['lease'], env=env)
        step('close_refuses_user_tab', refused.get('error') == 'not_agent_tab' and released.get('ok'),
             error=refused.get('error'))  # fmt: skip

        # Close a tab ca opened.
        spare = ca('open', url + 'other', '--instance', 'alpha', env=env, expect_ok=True)
        closed = ca('close', '--tab', spare['handle'], '--lease', spare['lease'], env=env)
        step('close_agent_tab', closed.get('closed') is True)

        # Disconnect: kill alpha's native host; the extension reconnects and the
        # claim (kept in session storage) still holds.
        host_pid = next(
            i['pid'] for i in ca('instances', env=env)['instances'] if i['label'] == 'alpha'
        )
        os.kill(host_pid, signal.SIGKILL)
        gone = ca('read', '--tab', tab, env=env)
        reconnected = wait_instances(env, 2, timeout=40, exclude_pid=host_pid)
        held = ca('status', '--tab', tab, '--lease', lease, env=env)
        read = ca('read', '--tab', tab, '--selector', '#notes', env=env)
        step('host_reconnects_with_claims', held.get('holds_lease') and read.get('ok'),
             during=gone.get('error'), old_pid=host_pid,
             new_pid=next(i['pid'] for i in reconnected if i['label'] == 'alpha'))  # fmt: skip

        # Activation alone preserves ownership and permits subsequent actions.
        port = devtools_port(profiles['alpha'])
        agent_page = page_target(port, lambda t: t['url'] == url)
        with urlopen(f'http://127.0.0.1:{port}/json/activate/{agent_page["id"]}') as response:
            response.read()
        time.sleep(0.5)
        status = ca('status', '--tab', tab, '--lease', lease, env=env)
        action = ca(
            'type', '--tab', tab, '--lease', lease, '--selector', '#title', 'watchable', env=env
        )
        step(
            'human_activation_preserves_lease',
            status.get('holds_lease') and action.get('ok'),
            status=status,
            action=action.get('error'),
        )

        # Activation during a slow open also preserves ownership.
        pending = {}
        opener = threading.Thread(target=lambda: pending.setdefault(
            'reply', ca('open', url + 'slow', '--instance', 'alpha', env=env)))  # fmt: skip
        opener.start()
        deadline = time.time() + 10
        slow = None
        while slow is None and time.time() < deadline:
            try:
                slow = page_target(port, lambda t: t['url'].endswith('/slow'))
            except LookupError:
                time.sleep(0.1)
        for target in (slow, agent_page):
            with urlopen(f'http://127.0.0.1:{port}/json/activate/{target["id"]}') as response:
                response.read()
        opener.join(30)
        reply = pending.get('reply', {})
        slow_action = (
            ca(
                'click',
                '--tab',
                reply.get('handle', ''),
                '--lease',
                reply.get('lease', ''),
                '--text',
                'Press me',
                env=env,
            )
            if reply.get('ok')
            else {}
        )
        step(
            'activation_while_opening_preserves_lease',
            reply.get('ok') and slow_action.get('ok'),
            error=reply.get('error'),
            action=slow_action.get('error'),
        )

        # Input after the document exists but before its slow subresource loads.
        loading_reply = {}
        loading_opener = threading.Thread(
            target=lambda: loading_reply.setdefault(
                'reply', ca('open', url + 'loading', '--instance', 'alpha', env=env)
            )
        )
        loading_opener.start()
        loading = None
        deadline = time.time() + 10
        while loading is None and time.time() < deadline:
            try:
                loading = page_target(port, lambda t: t['url'].endswith('/loading'))
            except LookupError:
                time.sleep(0.1)
        with urlopen(f'http://127.0.0.1:{port}/json/activate/{loading["id"]}') as response:
            response.read()
        time.sleep(1)
        human = DevTools(loading['webSocketDebuggerUrl'])
        human.call('Input.dispatchKeyEvent', type='keyDown', key='a', text='a')
        human.call('Input.dispatchKeyEvent', type='keyUp', key='a')
        human.close()
        loading_opener.join(30)
        taken = loading_reply.get('reply', {})
        kept = ca('status', '--tab', taken.get('handle', ''), '--lease', taken.get('lease', ''),
                  env=env)  # fmt: skip
        step(
            'human_input_while_opening_keeps_claim',
            taken.get('ok') and kept.get('holds_lease'),
            error=taken.get('error'),
            human_active=taken.get('human_active'),
        )
        with urlopen(f'http://127.0.0.1:{port}/json/activate/{agent_page["id"]}') as response:
            response.read()

        # Trusted input pauses only the tab it lands in; sibling tabs act without waiting.
        third = ca('open', url + 'other', '--instance', 'alpha', env=env, expect_ok=True)
        human = DevTools(agent_page['webSocketDebuggerUrl'])
        human.call('Input.dispatchKeyEvent', type='keyDown', key='a', text='a')
        human.call('Input.dispatchKeyEvent', type='keyUp', key='a')
        human.close()
        time.sleep(0.5)
        paused = ca('status', '--tab', tab, '--lease', lease, env=env)
        sibling = ca(
            'click',
            '--tab',
            third['handle'],
            '--lease',
            third['lease'],
            '--text',
            'Press me',
            env=env,
        )
        slow_status = ca(
            'status', '--tab', reply.get('handle', ''), '--lease', reply.get('lease', ''), env=env
        )
        step(
            'human_input_pauses_only_its_tab',
            paused.get('holds_lease')
            and paused.get('human_active')
            and sibling.get('ok')
            and 'human_waited_ms' not in sibling
            and slow_status.get('holds_lease')
            and not slow_status.get('human_active'),
            paused={k: paused.get(k) for k in ('holds_lease', 'human_active')},
            sibling=sibling.get('error'),
            slow=slow_status.get('holds_lease'),
        )

        check_pointer(env, url, work, port)
        check_cross_origin_editor(env, url)
        check_background_work(env, url, work, port, ids)
        if args.headed:
            check_agent_window_beside_other_app(env, url, devtools_port(profiles['beta']))

        if args.idle:
            pid = next(
                i['pid'] for i in ca('instances', env=env)['instances'] if i['label'] == 'alpha'
            )
            time.sleep(args.idle)
            later = next(
                i['pid'] for i in ca('instances', env=env)['instances'] if i['label'] == 'alpha'
            )
            step('service_worker_stays_connected_when_idle', pid == later, idle_seconds=args.idle)

        # Browser exit: its record disappears and its tabs are refused.
        browsers['beta'].terminate()
        browsers['beta'].wait(10)
        time.sleep(1)
        remaining = ca('instances', env=env)['instances']
        refused = ca('read', '--tab', beta['handle'], env=env)
        step('closed_browser_pruned', [i['label'] for i in remaining] == ['alpha']
             and refused.get('error') == 'instance_unavailable', error=refused.get('error'))  # fmt: skip
    finally:
        for process in browsers.values():
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(10)
        server.shutdown()
        leftovers = sorted(p.name for p in rendezvous.glob('*') if p.name != 'key')
        step('rendezvous_clean_after_exit', not leftovers, leftovers=leftovers)
        if args.evidence:
            args.evidence.write_text(json.dumps(RESULTS, indent=2) + '\n')
        shutil.rmtree(rendezvous, ignore_errors=True)
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
    failed = [r['check'] for r in RESULTS if not r['passed']]
    print(json.dumps({'ok': not failed, 'failed': failed, 'checks': len(RESULTS)}))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
