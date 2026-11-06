"""Browser bridge transport, native host relay, discovery and installation."""

import base64
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from computer_artist import browser as bridge  # noqa: E402
from computer_artist import browser_commands  # noqa: E402


def native_write(stream, value):
    data = json.dumps(value).encode()
    stream.write(struct.pack('=I', len(data)) + data)
    stream.flush()


def native_read(stream):
    header = stream.read(4)
    if len(header) < 4:
        return None
    return json.loads(stream.read(struct.unpack('=I', header)[0]))


class Transport(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='cab-'))

    def serve_once(self, key, handler):
        server, address = bridge.listen(self.root, 'abcdefgh-1')
        errors = []

        def run():
            connection, _ = server.accept()
            with connection:
                try:
                    bridge.handshake_server(connection, key)
                    handler(connection)
                except Exception as error:  # surfaced through the client side
                    errors.append(error)
            server.close()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return address, thread, errors

    def test_mutual_handshake_and_frames(self):
        key = bridge.auth_key(self.root)
        self.assertEqual(key, bridge.auth_key(self.root))
        if os.name != 'nt':
            self.assertEqual((self.root / 'key').stat().st_mode & 0o777, 0o600)

        def echo(connection):
            bridge.send_frame(connection, {'echo': bridge.recv_frame(connection)})

        address, thread, errors = self.serve_once(key, echo)
        with bridge.connect(address, key, 5) as sock:
            bridge.send_frame(sock, {'op': 'tabs'})
            self.assertEqual(bridge.recv_frame(sock), {'echo': {'op': 'tabs'}})
        thread.join(5)
        self.assertEqual(errors, [])

    def test_wrong_key_is_refused_before_any_request(self):
        handled = []
        address, thread, errors = self.serve_once(b'k' * 32, handled.append)
        with self.assertRaises((ConnectionError, OSError, bridge.BrowserError)):
            bridge.connect(address, b'x' * 32, 5)
        thread.join(5)
        self.assertEqual(handled, [])
        self.assertIsInstance(errors[0], PermissionError)

    def test_oversized_frames_are_refused(self):
        left, right = socket.socketpair()
        with left, right:
            left.sendall(struct.pack('>I', bridge.REQUEST_LIMIT + 1))
            with self.assertRaises(bridge.BrowserError) as raised:
                bridge.recv_frame(right, bridge.REQUEST_LIMIT)
            self.assertEqual(raised.exception.code, 'payload_too_large')
            with self.assertRaises(bridge.BrowserError):
                bridge.send_frame(left, {'text': 'x' * 100}, limit=50)


class HostRelay(unittest.TestCase):
    """Run the real host process with this test acting as Chrome and the extension."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='cab-'))
        env = {**os.environ, 'CA_BROWSER_DIR': str(self.root), 'PYTHONPATH': str(ROOT)}
        self.host = subprocess.Popen(
            [sys.executable, '-m', 'computer_artist.browser_host', 'chrome-extension://x/'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env=env,
        )
        self.addCleanup(self.stop)

    def stop(self):
        if self.host.poll() is None:
            self.host.kill()
        self.host.wait(5)
        self.host.stdin.close()
        self.host.stdout.close()

    def hello(self, **extra):
        native_write(self.host.stdin, {'type': 'hello', 'protocol': bridge.PROTOCOL,
                                       'instance': 'a1b2c3d4', 'label': 'work', **extra})  # fmt: skip
        return native_read(self.host.stdout)

    def test_relays_requests_and_cleans_up_on_disconnect(self):
        ready = self.hello()
        self.assertEqual(ready['type'], 'ready')
        [record] = bridge.records(self.root)
        self.assertEqual((record['instance'], record['label']), ('a1b2c3d4', 'work'))
        replies = {}

        def cli():
            replies['tabs'] = bridge.call(record, {'op': 'tabs'}, 5, bridge.auth_key(self.root))

        thread = threading.Thread(target=cli)
        thread.start()
        request = native_read(self.host.stdout)
        self.assertEqual((request['type'], request['op']), ('request', 'tabs'))
        native_write(self.host.stdin, {'type': 'reply', 'id': request['id'], 'ok': True,
                                       'tabs': [{'tab': 7}]})  # fmt: skip
        thread.join(5)
        self.assertEqual(replies['tabs'], {'ok': True, 'tabs': [{'tab': 7}]})
        # Chrome closing the port ends the host and withdraws its endpoint.
        self.host.stdin.close()
        self.assertEqual(self.host.wait(5), 0)
        self.assertEqual(bridge.records(self.root), [])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['key'])

    def test_pending_request_fails_when_browser_goes_away(self):
        self.hello()
        [record] = bridge.records(self.root)
        replies = {}
        thread = threading.Thread(
            target=lambda: replies.setdefault(
                'r', bridge.call(record, {'op': 'tabs'}, 30, bridge.auth_key(self.root))
            )
        )
        thread.start()
        native_read(self.host.stdout)
        self.host.stdin.close()
        thread.join(10)
        self.assertEqual(replies['r']['error'], 'instance_disconnected')

    def test_refuses_other_protocol_versions(self):
        reply = self.hello(protocol=bridge.PROTOCOL + 1)
        self.assertEqual(reply['error'], 'protocol_mismatch')
        self.assertEqual(self.host.wait(5), 2)
        self.assertEqual(bridge.records(self.root), [])


class Discovery(unittest.TestCase):
    def test_stale_records_are_pruned_and_never_selected(self):
        root = Path(tempfile.mkdtemp(prefix='cab-'))
        bridge.auth_key(root)
        if os.name == 'nt':
            dead = 'tcp:127.0.0.1:9'
        else:
            dead = f'unix:{root}/deadbeef-1.sock'
        bridge.write_record(root, {'instance': 'deadbeef', 'address': dead, 'pid': 1})
        self.assertEqual(bridge.live_instances(root), [])
        self.assertFalse((root / 'deadbeef.json').exists())
        with self.assertRaises(bridge.BrowserError) as raised:
            bridge.select_instance([])
        self.assertEqual(raised.exception.code, 'no_instances')
        # Agents are told that ca starts no browser and that a new one needs the user.
        self.assertIn('never starts a browser', str(raised.exception))

    def test_selection_requires_an_explicit_choice_between_instances(self):
        found = [
            {'instance': 'aaaaaaaa', 'label': 'work'},
            {'instance': 'bbbbbbbb', 'label': 'work'},
        ]
        with self.assertRaises(bridge.BrowserError) as raised:
            bridge.select_instance(found)
        self.assertEqual(raised.exception.code, 'ambiguous_instance')
        with self.assertRaises(bridge.BrowserError):
            bridge.select_instance(found, 'work')
        self.assertEqual(bridge.select_instance(found, 'bbbbbbbb')['instance'], 'bbbbbbbb')
        # A host that exists but did not answer keeps the choice explicit.
        silent = [{'instance': 'cccccccc', 'error': 'instance_unavailable'}]
        with self.assertRaises(bridge.BrowserError) as raised:
            bridge.select_instance(found[:1], None, silent)
        self.assertEqual(raised.exception.code, 'ambiguous_instance')
        self.assertEqual(bridge.select_instance(found[:1], 'aaaaaaaa', silent)['label'], 'work')
        self.assertEqual(bridge.parse_tab('aaaaaaaa:12'), ('aaaaaaaa', 12))
        with self.assertRaises(bridge.BrowserError) as raised:
            bridge.parse_tab('aaaaaaaa:12', 'bbbbbbbb')
        self.assertEqual(raised.exception.code, 'wrong_instance')


class PointerRequests(unittest.TestCase):
    """CLI-side validation and request shape for coordinates, gestures and frames."""

    def operate(self, *argv, version='0.6.0', capabilities=()):
        import argparse

        parser = argparse.ArgumentParser()
        browser_commands.add_command(parser.add_subparsers(dest='command'))
        args = parser.parse_args(['browser', *argv])
        sent = []
        instance = {'instance': 'aaaaaaaa', 'extension_version': version, 'address': 'unix:x',
                    'capabilities': list(capabilities)}  # fmt: skip

        def call(record, request, timeout):
            sent.append(request)
            return {'ok': True}

        with (
            mock.patch.object(bridge, 'live_instances', return_value=[instance]),
            mock.patch.object(bridge, 'call', side_effect=call),
        ):
            reply = browser_commands.dispatch(args)
        return reply, (sent[0] if sent else None)

    def test_point_click_inside_a_frame(self):
        reply, request = self.operate(
            'click', '--tab', 'aaaaaaaa:5', '--lease', 'L', '--frame', '#sketch',
            '--selector', 'canvas', '--x', '12.5', '--y', '40', '--count', '2',
            '--button', 'right', '--modifiers', 'shift+Ctrl',
        )  # fmt: skip
        self.assertTrue(reply['ok'])
        self.assertEqual(request['frame'], ['#sketch'])
        self.assertEqual((request['x'], request['y'], request['count']), (12.5, 40.0, 2))
        self.assertEqual(request['button'], 'right')
        self.assertEqual(request['modifiers'], ['Control', 'Shift'])

    def test_zero_coordinates_are_sent(self):
        reply, request = self.operate(
            'click', '--tab', 'aaaaaaaa:5', '--lease', 'L', '--selector', 'canvas',
            '--x', '0', '--y', '0', version='0.5.3',
        )  # fmt: skip
        self.assertEqual(reply['error'], 'extension_outdated')
        _, request = self.operate(
            'move', '--tab', 'aaaaaaaa:5', '--lease', 'L', '--x', '0', '--y', '9'
        )
        self.assertEqual((request['x'], request['y']), (0.0, 9.0))

    def test_drag_path_keeps_order(self):
        _, request = self.operate(
            'drag', '--tab', 'aaaaaaaa:5', '--lease', 'L', '--from', '1', '2',
            '--via', '3', '4', '--via', '5', '6', '--to', '7', '8',
        )  # fmt: skip
        self.assertEqual(request['path'], [[1, 2], [3, 4], [5, 6], [7, 8]])

    def test_invalid_input_is_refused_before_the_browser(self):
        cases = [
            (('click', '--tab', 'aaaaaaaa:5', '--lease', 'L'), 'invalid_target'),
            (('click', '--tab', 'aaaaaaaa:5', '--lease', 'L', '--x', '3'), 'invalid_point'),
            (('move', '--tab', 'aaaaaaaa:5', '--lease', 'L', '--x', 'nan', '--y', '1'),
             'invalid_point'),
            (('click', '--tab', 'aaaaaaaa:5', '--lease', 'L', '--text', 'Go', '--modifiers',
              'hyper'), 'invalid_modifier'),
        ]  # fmt: skip
        for argv, code in cases:
            reply, request = self.operate(*argv)
            self.assertEqual(reply['error'], code, argv)
            self.assertIsNone(request)

    def test_viewport_guard_is_sent_only_to_extensions_that_check_it(self):
        drag = ('drag', '--tab', 'aaaaaaaa:5', '--lease', 'L', '--selector', 'canvas',
                '--from', '10', '10', '--to', '50', '50', '--viewport', '2560x1267')  # fmt: skip
        _, request = self.operate(*drag, version='0.9.0', capabilities=['viewport_guard'])
        self.assertEqual(request['viewport'], {'width': 2560, 'height': 1267})
        # An older extension would ignore the size and act on stale coordinates.
        reply, request = self.operate(*drag, version='0.8.0', capabilities=['pointer'])
        self.assertEqual((reply['error'], request), ('extension_outdated', None))
        reply, _ = self.operate('click', '--tab', 'aaaaaaaa:5', '--lease', 'L', '--x', '1',
                                '--y', '1', '--viewport', 'wide', capabilities=['viewport_guard'])  # fmt: skip
        self.assertEqual(reply['error'], 'invalid_viewport')

    def test_scroll_needs_no_target(self):
        _, request = self.operate('scroll', '--tab', 'aaaaaaaa:5', '--lease', 'L', '--delta', '300')
        self.assertEqual((request['op'], request['delta']), ('scroll', 300.0))

    def test_older_extensions_refuse_new_options_but_keep_plain_clicks(self):
        reply, request = self.operate(
            'click', '--tab', 'aaaaaaaa:5', '--lease', 'L', '--text', 'Go', '--x', '1', '--y', '1',
            version='0.5.3',
        )  # fmt: skip
        self.assertEqual((reply['error'], reply['required']), ('extension_outdated', '0.6.0'))
        self.assertIsNone(request)
        reply, request = self.operate(
            'drag', '--tab', 'aaaaaaaa:5', '--lease', 'L', '--from', '1', '1', '--to', '2', '2',
            version='0.5.3',
        )  # fmt: skip
        self.assertEqual(reply['error'], 'extension_outdated')
        reply, request = self.operate(
            'click', '--tab', 'aaaaaaaa:5', '--lease', 'L', '--text', 'Go', version='0.5.3'
        )
        self.assertTrue(reply['ok'])
        self.assertEqual(set(request), {'op', 'tab', 'lease', 'text'})

    def test_screenshot_reports_scale(self):
        png = b'\x89PNG\r\n\x1a\n' + struct.pack('>I4sII', 13, b'IHDR', 2560, 1323)
        folder = Path(tempfile.mkdtemp(prefix='cab-'))
        reply = browser_commands.save_png(
            {'ok': True, 'data_url': 'data:image/png;base64,' + base64.b64encode(png).decode(),
             'viewport': {'width': 1280, 'height': 661}},
            folder / 'shot.png',
        )  # fmt: skip
        self.assertEqual((reply['width'], reply['scale']), (2560, 2.0))


class Maintenance(unittest.TestCase):
    """Lease journal cleanup, capability gates, reload decisions and build identity."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix='cab-'))
        patch = mock.patch.dict(os.environ, {'CA_BROWSER_DATA_DIR': str(self.home / 'data')})
        patch.start()
        self.addCleanup(patch.stop)

    def run_cli(self, argv, instances, replies):
        import argparse

        parser = argparse.ArgumentParser()
        browser_commands.add_command(parser.add_subparsers(dest='command'))
        sent = []

        def call(record, request, timeout):
            sent.append(request)
            return replies(request)

        with (
            mock.patch.object(bridge, 'live_instances', return_value=instances),
            mock.patch.object(bridge, 'call', side_effect=call),
        ):
            return browser_commands.dispatch(parser.parse_args(['browser', *argv])), sent

    def test_cleanup_ends_only_the_named_owners_recorded_leases(self):
        current = {'instance': 'aaaaaaaa', 'extension_version': '0.7.0', 'address': 'unix:x'}
        for tab, owner, action in ((1, 'mine', 'open'), (2, 'other', 'open'), (3, 'mine', 'claim')):
            lease = f'{tab:024x}'
            browser_commands.journal('aaaaaaaa', tab, lease, {'claim': {'owner': owner}}, action)
        # Another owner's later lease on tab 1 is a separate record that cleanup keeps.
        browser_commands.journal('aaaaaaaa', 1, 'f' * 24, {'claim': {'owner': 'other'}}, 'claim')

        def replies(request):
            if request['op'] == 'status':
                return {'ok': True, 'exists': True, 'holds_lease': request['tab'] != 3}
            return {'ok': True}

        reply, sent = self.run_cli(['cleanup', '--owner', 'mine'], [current], replies)
        self.assertTrue(reply['ok'])
        acted = [(r['op'], r['tab'], r['lease']) for r in sent if r['op'] != 'status']
        self.assertEqual(acted, [('close', 1, f'{1:024x}')])
        self.assertEqual(reply['tabs'][1]['result'], 'ended')
        left = sorted(p.name for p in browser_commands.lease_dir().glob('*.json'))
        self.assertEqual(left, [f'aaaaaaaa-1-{"f" * 24}.json', f'aaaaaaaa-2-{2:024x}.json'])
        if os.name != 'nt':  # Windows has no POSIX modes
            self.assertEqual(oct(browser_commands.lease_dir().stat().st_mode & 0o777), '0o700')

    def test_cleanup_closes_tabs_the_extension_says_the_owner_opened(self):
        # Extension 0.10.0 remembers who opened a tab, so a tab opened, released and
        # claimed again is closed, and a user's tab claimed by this owner only released.
        current = {'instance': 'aaaaaaaa', 'extension_version': '0.10.0', 'address': 'unix:x'}
        for tab in (5, 6, 7):
            browser_commands.journal(
                'aaaaaaaa', tab, f'{tab:024x}', {'claim': {'owner': 'mine'}}, 'claim'
            )
        opened_by = {5: 'mine', 6: None, 7: 'someone-else'}

        def replies(request):
            if request['op'] == 'status':
                return {
                    'ok': True,
                    'exists': True,
                    'holds_lease': True,
                    'opened_by': opened_by[request['tab']],
                }
            return {'ok': True}

        reply, sent = self.run_cli(['cleanup', '--owner', 'mine'], [current], replies)
        self.assertTrue(reply['ok'])
        acted = [(r['op'], r['tab']) for r in sent if r['op'] != 'status']
        self.assertEqual(acted, [('close', 5), ('release', 6), ('release', 7)])

    def test_cleanup_keeps_records_it_could_not_check(self):
        current = {'instance': 'aaaaaaaa', 'extension_version': '0.7.0', 'address': 'unix:x'}
        browser_commands.journal('aaaaaaaa', 4, 'a' * 24, {'claim': {'owner': 'mine'}}, 'open')
        reply, _ = self.run_cli(['cleanup', '--owner', 'mine'], [current],
                                lambda r: {'ok': False, 'error': 'instance_disconnected'})  # fmt: skip
        self.assertFalse(reply['ok'])
        self.assertEqual(len(list(browser_commands.lease_dir().glob('*.json'))), 1)

    def test_agent_window_needs_the_capability(self):
        old = {'instance': 'aaaaaaaa', 'extension_version': '0.6.0', 'address': 'unix:x'}
        reply, sent = self.run_cli(['open', 'https://example.com', '--agent-window'], [old],
                                   lambda r: {'ok': True})  # fmt: skip
        self.assertEqual(reply['error'], 'extension_outdated')
        self.assertEqual(sent, [])
        new = {**old, 'capabilities': ['agent_window']}
        reply, sent = self.run_cli(['open', 'https://example.com', '--agent-window'], [new],
                                   lambda r: {'ok': True, 'tab': 7, 'lease': 'L'})  # fmt: skip
        self.assertTrue(sent[0]['agent_window'])

    def test_reload_is_skipped_manual_or_self(self):
        browser_commands.copy_extension(self.home / 'data' / 'chrome-extension')
        built = browser_commands.build_id()
        self.assertEqual(
            browser_commands.installed_build(self.home / 'data' / 'chrome-extension'), built
        )
        same = {'instance': 'aaaaaaaa', 'build': built, 'address': 'unix:x'}
        reply, sent = self.run_cli(['reload'], [same], lambda r: {'ok': True})
        self.assertEqual((reply['reloaded'], sent), (False, []))
        old = {
            'instance': 'aaaaaaaa',
            'build': None,
            'extension_version': '0.6.0',
            'address': 'unix:x',
        }
        reply, sent = self.run_cli(['reload'], [old], lambda r: {'ok': True})
        self.assertEqual((reply['error'], sent), ('manual_reload_needed', []))
        busy = {**old, 'capabilities': ['reload']}
        reply, _ = self.run_cli(['reload'], [busy], lambda r: {'ok': False, 'error': 'busy'})
        self.assertEqual(reply['error'], 'busy')

    def test_build_is_stamped_and_reproducible(self):
        first = browser_commands.package(self.home / 'a')
        second = browser_commands.package(self.home / 'b')
        self.assertEqual(first['sha256'], second['sha256'])
        import zipfile

        with zipfile.ZipFile(first['path']) as archive:
            worker = archive.read('background.js').decode()
        self.assertIn(f"const BUILD = '{browser_commands.build_id()}'", worker)
        source = (browser_commands.EXTENSION_SOURCE / 'background.js').read_text()
        self.assertIn(f"const BUILD = '{browser_commands.BUILD_MARK}'", source)

    def test_host_restart_resends_only_safe_requests(self):
        record = {'instance': 'aaaaaaaa', 'address': 'unix:old'}
        moved = {'instance': 'aaaaaaaa', 'address': 'unix:new'}
        calls = []

        def call(instance, request, timeout):
            calls.append(instance['address'])
            if instance['address'] == 'unix:old':
                raise bridge.BrowserError('instance_disconnected', 'gone')
            return {'ok': True}

        with (
            mock.patch.object(bridge, 'call', side_effect=call),
            mock.patch.object(bridge, 'live_instances', return_value=[moved]),
            mock.patch('time.sleep'),
        ):
            self.assertTrue(
                browser_commands.call_with_recovery(record, {'op': 'read'}, 5, True)['ok']
            )
            with self.assertRaises(bridge.BrowserError):
                browser_commands.call_with_recovery(record, {'op': 'click'}, 5, False)
        self.assertEqual(calls, ['unix:old', 'unix:new', 'unix:old'])

    def test_host_disconnect_replies_resend_only_when_safe(self):
        record = {'instance': 'aaaaaaaa', 'address': 'unix:old'}
        moved = {'instance': 'aaaaaaaa', 'address': 'unix:new'}

        def call(instance, request, timeout):
            if instance['address'] == 'unix:old':
                return {'ok': False, 'error': 'instance_disconnected', 'delivered': request['sent']}
            return {'ok': True}

        recover = browser_commands.call_with_recovery
        with (
            mock.patch.object(bridge, 'call', side_effect=call),
            mock.patch.object(bridge, 'live_instances', return_value=[moved]),
            mock.patch('time.sleep'),
        ):
            self.assertTrue(recover(record, {'op': 'read', 'sent': True}, 5, True)['ok'])
            self.assertTrue(recover(record, {'op': 'click', 'sent': False}, 5, False)['ok'])
            self.assertFalse(recover(record, {'op': 'click', 'sent': True}, 5, False)['ok'])


@unittest.skipIf(os.name == 'nt', 'Windows registers hosts in the registry')
class Install(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix='cab-'))
        patch = mock.patch.dict(os.environ, {
            'CA_BROWSER_DATA_DIR': str(self.home / 'data'),
            'XDG_CONFIG_HOME': str(self.home / 'config'),
            'HOME': str(self.home),
        })  # fmt: skip
        patch.start()
        self.addCleanup(patch.stop)

    def install(self, **values):
        defaults = {'browser': None, 'user_data_dir': None, 'dry_run': False, 'remove': False}
        return browser_commands.install(Namespace(**{**defaults, **values}))

    def test_stable_extension_id(self):
        # The native host's allowed origin depends on this; changing the key breaks installs.
        manifest = browser_commands.bundled_manifest()
        self.assertEqual(
            browser_commands.extension_id(manifest), 'fbbljgpbpgepkaimgbpcpemjdllnname'
        )

    def test_scoped_install_touches_only_the_named_profile(self):
        if sys.platform == 'darwin':
            browser = self.home / 'Library/Application Support/Google/Chrome'
        else:
            browser = self.home / 'config/google-chrome'
        browser.mkdir(parents=True)
        profile = self.home / 'profile'
        reply = self.install(user_data_dir=[profile])
        self.assertEqual([h['browser'] for h in reply['hosts']], [f'user-data-dir:{profile}'])
        self.assertFalse((browser / 'NativeMessagingHosts').exists())
        manifest = json.loads(
            (profile / 'NativeMessagingHosts' / browser_commands.HOST_FILE).read_text()
        )
        self.assertEqual(
            manifest['allowed_origins'], [f'chrome-extension://{reply["extension_id"]}/']
        )
        self.assertTrue(os.access(manifest['path'], os.X_OK))
        self.assertTrue((Path(reply['extension_dir']) / 'background.js').is_file())
        # A bare install registers detected browsers only.
        reply = self.install()
        self.assertEqual([h['browser'] for h in reply['hosts']], ['chrome'])
        self.assertTrue(browser_commands.doctor()['checks'][1]['passed'])

    def test_unrelated_manifests_are_neither_replaced_nor_removed(self):
        profile = self.home / 'profile'
        path = profile / 'NativeMessagingHosts' / browser_commands.HOST_FILE
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'name': 'something.else', 'path': '/bin/true'}))
        with self.assertRaises(ValueError):
            self.install(user_data_dir=[profile])
        reply = self.install(user_data_dir=[profile], remove=True)
        self.assertEqual(reply['hosts'][0]['action'], 'kept_unowned')
        self.assertTrue(path.exists())

    def test_package_is_reproducible(self):
        first = browser_commands.package(self.home / 'a')
        second = browser_commands.package(self.home / 'b')
        self.assertEqual(first['sha256'], second['sha256'])
        self.assertIn('manifest.json', first['files'])


@unittest.skipUnless(os.name == 'nt', 'Windows paths; Linux/macOS use manifest folders')
class WindowsRegistry(unittest.TestCase):
    """Registry registration and ownership-checked removal, with a fake winreg."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix='cab-'))
        (self.home / 'Google/Chrome/User Data').mkdir(parents=True)
        self.keys = {}
        keys = self.keys

        class Key:
            def __init__(self, path):
                self.path = path

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def open_key(root, path):
            if path not in keys:
                raise FileNotFoundError(path)
            return Key(path)

        def delete_key(root, path):
            del keys[path]

        fake = types.SimpleNamespace(
            HKEY_CURRENT_USER=1,
            REG_SZ=1,
            CreateKey=lambda root, path: (keys.setdefault(path, None), Key(path))[1],
            SetValueEx=lambda key, name, reserved, kind, value: keys.__setitem__(key.path, value),
            OpenKey=open_key,
            QueryValue=lambda key, name: keys[key.path],
            DeleteKey=delete_key,
        )
        patches = [
            mock.patch.dict(sys.modules, {'winreg': fake}),
            mock.patch.dict(
                os.environ,
                {'CA_BROWSER_DATA_DIR': str(self.home / 'data'), 'LOCALAPPDATA': str(self.home)},
            ),  # fmt: skip
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def run_install(self, remove=False):
        args = Namespace(browser=None, user_data_dir=None, dry_run=False, remove=remove)
        return browser_commands.install(args)

    def test_fresh_install_registers_and_removal_respects_ownership(self):
        reply = self.run_install()
        [(location, manifest)] = self.keys.items()
        self.assertIn(r'Google\Chrome\NativeMessagingHosts', location)
        self.assertEqual(json.loads(Path(manifest).read_text())['path'], reply['host'])
        # Another installation took the registration over: removal leaves it.
        other = self.home / 'other.json'
        other.write_text(json.dumps({'name': bridge.HOST_NAME, 'path': 'C:/elsewhere.bat'}))
        self.keys[location] = str(other)
        self.assertEqual(self.run_install(remove=True)['hosts'][0]['action'], 'kept_unowned')
        self.assertIn(location, self.keys)
        self.keys[location] = manifest
        self.assertEqual(self.run_install(remove=True)['hosts'][0]['action'], 'remove')
        self.assertEqual(self.keys, {})


if __name__ == '__main__':
    unittest.main()
