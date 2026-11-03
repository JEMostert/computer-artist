"""Browser bridge transport, native host relay, discovery and installation."""

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
