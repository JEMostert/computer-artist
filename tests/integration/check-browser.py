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
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

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
    )
    return found[-1] if found else shutil.which('chromium') or shutil.which('chromium-browser')


def launch(chrome, profile, extension, env):
    command = [
        chrome,
        '--headless=new',
        f'--user-data-dir={profile}',
        # Never ask the desktop keyring (a GNOME unlock prompt would take focus).
        '--password-store=basic',
        '--use-mock-keychain',
        '--no-first-run',
        '--no-default-browser-check',
        f'--disable-extensions-except={extension}',
        f'--load-extension={extension}',
        '--remote-debugging-port=0',
        'about:blank',
    ]
    return subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)  # fmt: skip


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


# --------------------------------------------------------------------- main


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--chrome', default=os.environ.get('CA_TEST_CHROME') or default_chrome())
    parser.add_argument(
        '--idle', type=float, default=40, help='Idle seconds for the keepalive check'
    )
    parser.add_argument('--evidence', type=Path, help='Write the JSON results here')
    parser.add_argument('--keep', action='store_true', help='Keep temporary folders')
    args = parser.parse_args()
    if not args.chrome or not Path(args.chrome).is_file():
        print('Chrome for Testing or Chromium not found; pass --chrome PATH', file=sys.stderr)
        return 2
    work = Path(tempfile.mkdtemp(prefix='ca-browser-check-'))
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR') or work)
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
            browsers[name] = launch(args.chrome, profile, extension, env)
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

        options = ca('locate', '--tab', tab, '--selector', '#colour', env=env, expect_ok=True)['elements'][0]
        selected = ca('select', '--tab', tab, '--lease', lease, '--selector', '#colour', 'blue', env=env)
        unchanged = ca('select', '--tab', tab, '--lease', lease, '--selector', '#colour', 'blue', env=env)
        after_select = ca('locate', '--tab', tab, '--selector', '#colour', env=env, expect_ok=True)['elements'][0]
        events = ca('read', '--tab', tab, '--selector', '#select-events', env=env, expect_ok=True)['text']
        step('select_readback_and_events', options['value'] == 'red' and len(options['options']) == 6
             and selected.get('changed') is True and unchanged.get('changed') is False
             and after_select['value'] == 'blue' and after_select['options'][1]['selected']
             and after_select['options'][2]['disabled'] and after_select['options'][3]['disabled']
             and events == 'input,change', events=events, options=after_select['options'])
        for selector, value, error in [
            ('#colour', 'missing', 'option_not_found'),
            ('#colour', 'disabled', 'element_not_interactable'),
            ('#colour', 'grouped', 'element_not_interactable'),
            ('#colour', 'duplicate', 'ambiguous_target'),
            ('#disabled-select', 'x', 'element_not_interactable'),
            ('#multiple', 'x', 'unsupported_control'),
            ('#title', 'x', 'unsupported_control'),
        ]:
            result = ca('select', '--tab', tab, '--lease', lease, '--selector', selector, value, env=env)
            step('select_refuses_' + selector[1:] + '_' + value, result.get('error') == error,
                 expected=error, actual=result.get('error'))

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
        step('background_screenshot_refused', hidden.get('error') == 'tab_not_visible')
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
        # The human clicks inside the page's iframe: the lease ends at once.
        frame = tools.call('Runtime.evaluate', returnByValue=True, expression=(
            'JSON.stringify(document.getElementById("frame").getBoundingClientRect())'
        ))['result']['value']  # fmt: skip
        box = json.loads(frame)
        x, y = box['x'] + 20, box['y'] + 20
        for kind in ('mousePressed', 'mouseReleased'):
            tools.call('Input.dispatchMouseEvent', type=kind, x=x, y=y, button='left', clickCount=1)
        time.sleep(0.5)
        status = ca('status', '--tab', existing['handle'], '--lease', claimed['lease'], env=env)
        refused = ca('click', '--tab', existing['handle'], '--lease', claimed['lease'], '--text',
                     'Add note', env=env)  # fmt: skip
        step('human_input_in_iframe_revokes', status.get('stop_reason') == 'human_input'
             and refused.get('error') == 'ownership_revoked', status=status.get('stop_reason'))  # fmt: skip
        # Claimed again, then the human types in the top document.
        claimed = ca('claim', '--tab', existing['handle'], env=env, expect_ok=True)
        tools.call('Input.dispatchKeyEvent', type='keyDown', key='a', text='a')
        tools.call('Input.dispatchKeyEvent', type='keyUp', key='a')
        tools.close()
        time.sleep(0.5)
        status = ca('status', '--tab', existing['handle'], '--lease', claimed['lease'], env=env)
        step('human_key_revokes', status.get('stop_reason') == 'human_input')

        # Close a tab ca opened; refuse to close one it merely claimed.
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
        action = ca('type', '--tab', tab, '--lease', lease, '--selector', '#title', 'watchable', env=env)
        step('human_activation_preserves_lease', status.get('holds_lease') and action.get('ok'),
             status=status, action=action.get('error'))

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
        slow_action = ca('click', '--tab', reply.get('handle', ''), '--lease', reply.get('lease', ''),
                         '--text', 'Press me', env=env) if reply.get('ok') else {}
        step('activation_while_opening_preserves_lease', reply.get('ok') and slow_action.get('ok'),
             error=reply.get('error'), action=slow_action.get('error'))

        # Input after the document exists but before its slow subresource loads.
        loading_reply = {}
        loading_opener = threading.Thread(target=lambda: loading_reply.setdefault(
            'reply', ca('open', url + 'loading', '--instance', 'alpha', env=env)))
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
        step('human_input_while_opening_refuses_ownership',
             taken.get('error') == 'ownership_revoked' and taken.get('stop_reason') == 'human_input',
             result=taken)
        with urlopen(f'http://127.0.0.1:{port}/json/activate/{agent_page["id"]}') as response:
            response.read()

        # Trusted input revokes only the active tab, leaving two sibling leases intact.
        third = ca('open', url + 'other', '--instance', 'alpha', env=env, expect_ok=True)
        human = DevTools(agent_page['webSocketDebuggerUrl'])
        human.call('Input.dispatchKeyEvent', type='keyDown', key='a', text='a')
        human.call('Input.dispatchKeyEvent', type='keyUp', key='a')
        human.close()
        time.sleep(0.5)
        revoked = ca('status', '--tab', tab, '--lease', lease, env=env)
        sibling = ca('click', '--tab', third['handle'], '--lease', third['lease'], '--text', 'Press me', env=env)
        slow_status = ca('status', '--tab', reply.get('handle', ''), '--lease', reply.get('lease', ''), env=env)
        step('human_input_only_revokes_relevant_tab', revoked.get('stop_reason') == 'human_input'
             and sibling.get('ok') and slow_status.get('holds_lease'), revoked=revoked.get('stop_reason'),
             sibling=sibling.get('error'), slow=slow_status.get('holds_lease'))

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
