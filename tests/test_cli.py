import json
import os
import socketserver
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from computer_artist.diagnostics import plugin_source_hash

ROOT = Path(__file__).resolve().parents[1]


class CLITest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.events = []
        self.acquired = threading.Event()
        self.returned = threading.Event()
        self.reject_focus = False
        self.old_plugin = False
        self.agent_keyboard = False
        self.agent_keyboard_ready = True
        self.acquire_reply_delay = 0
        self.window_ids = ['target']
        self.window_titles = {'target': 'Untitled — KolourPaint'}
        test = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                lease = ''
                self.lease = ''
                for line in self.rfile:
                    request = json.loads(line)
                    test.events.append(request)
                    op = request['op']
                    if op == 'acquire':
                        lease = 'test-lease'
                        self.lease = lease
                        test.acquired.set()
                        time.sleep(test.acquire_reply_delay)
                    if op in ('release', 'takeover'):
                        lease = ''
                        test.returned.set()
                    self.lease = lease
                    reply = {'ok': True, 'lease': lease, 'generation': 2, 'keyboard_ready': True}
                    if request.get('lane', 'agent') == 'agent':
                        reply['keyboard_ready'] = test.agent_keyboard_ready
                    if op == 'windows':
                        reply['windows'] = [
                            {
                                'id': identity,
                                'title': test.window_titles.get(identity, identity),
                                'native': True,
                                'visible': True,
                                request.get('lane', 'agent'): bool(lease),
                                'x': 100,
                                'y': 200,
                                'width': 300,
                                'height': 400,
                            }
                            for identity in test.window_ids
                        ]
                    if op == 'capabilities':
                        reply['keyboard'] = test.agent_keyboard
                        reply['operations'] = [
                            'capture',
                            'focus',
                            'key',
                            'clipboard_get',
                            'clipboard_set',
                        ]
                        if not test.old_plugin:
                            reply.update(
                                protocol=3,
                                lane=request.get('lane', 'agent'),
                                host_pointer=True,
                                build={
                                    'source_sha256': plugin_source_hash(ROOT),
                                    'kwin_headers': '6.7.5',
                                    'kwin_running': '6.7.5',
                                },
                            )
                    if op == 'focus' and test.reject_focus:
                        reply.update(ok=False, error='rejected')
                    try:
                        self.wfile.write((json.dumps(reply) + '\n').encode())
                    except (BrokenPipeError, ConnectionResetError):
                        break

            def finish(self):
                try:
                    super().finish()
                finally:
                    # KWin returns ownership on disconnect even if termination
                    # interrupted the acquire reply before the client got its lease.
                    if getattr(self, 'lease', ''):
                        test.returned.set()

        self.server = socketserver.ThreadingUnixStreamServer(str(self.path / 'control'), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.env = dict(
            os.environ,
            CA_SOCKET=str(self.path / 'control'),
            CA_WINDOW_DIR=str(self.path / 'window'),
            CA_OUTPUT_DIR=str(self.path / 'output'),
        )

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def ca(self, *args):
        return subprocess.run(
            [str(ROOT / 'ca'), *args], env=self.env, capture_output=True, text=True, timeout=8
        )

    def test_click_translates_window_coordinates_and_releases(self):
        result = self.ca('click', '--window', 'target', '--x', '20', '--y', '30')
        self.assertEqual(result.returncode, 0, result.stderr)
        motion = next(e for e in self.events if e['op'] == 'move')
        self.assertEqual((motion['x'], motion['y']), (120, 230))
        self.assertEqual(motion['lease'], 'test-lease')
        self.assertEqual(self.events[-1]['op'], 'release')
        self.assertEqual(json.loads(result.stdout)['status'], 'dispatched')

    def test_type_focuses_without_mouse_events(self):
        result = self.ca('--host', 'type', '--window', 'target', 'Ab 😀')
        self.assertEqual(result.returncode, 0, result.stderr)
        ops = [e['op'] for e in self.events]
        self.assertLess(ops.index('focus'), ops.index('key'))
        self.assertLess(ops.index('clipboard_set'), ops.index('key'))
        self.assertEqual(
            next(e['text'] for e in self.events if e['op'] == 'clipboard_set'), 'Ab 😀'
        )
        self.assertEqual([e['code'] for e in self.events if e['op'] == 'key'], [42, 110, 110, 42])
        self.assertFalse(any(op in ('move', 'button') for op in ops))
        self.assertEqual(ops[-1], 'release')

    def test_doctor_does_not_create_a_session_or_send_input(self):
        result = self.ca('doctor', '--window', 'target')
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report['selected_window']['agent_candidate'])
        self.assertTrue(all(event['op'] in ('capabilities', 'windows') for event in self.events))
        self.assertFalse((self.path / 'window').exists())
        self.assertFalse((self.path / 'output').exists())

    def test_invalid_svg_rejected_before_connecting(self):
        result = self.ca('draw', '--window', 'target', '--path', 'M10 10 C20 20')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.events, [])
        self.assertFalse((self.path / 'output').exists())

    def test_svg_fifo_is_rejected_without_blocking_or_connecting(self):
        source = self.path / 'path.fifo'
        os.mkfifo(source)
        result = self.ca('draw', '--window', 'target', '--path-file', str(source))
        self.assertEqual(result.returncode, 1)
        self.assertIn('regular', result.stderr)
        self.assertEqual(self.events, [])

    def test_oversized_text_has_no_side_effects(self):
        result = self.ca('--host', 'type', '--window', 'target', '😀' * 2049)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.events, [])

    def test_rejected_focus_returns_lease_without_typing(self):
        self.reject_focus = True
        result = self.ca('--host', 'type', '--window', 'target', 'hello')
        self.assertEqual(result.returncode, 1)
        self.assertFalse(any(e['op'] == 'key' for e in self.events))
        self.assertFalse(any(e['op'] == 'clipboard_set' for e in self.events))
        self.assertEqual(self.events[-1]['op'], 'release')

    def test_keyboard_and_clipboard_require_host(self):
        for args in [
            ('paste', '--window', 'target', 'hello'),
            ('key', '--window', 'target', 'W'),
            ('clipboard', 'set', 'hello'),
        ]:
            self.events.clear()
            result = self.ca(*args)
            self.assertEqual(result.returncode, 1)
            self.assertFalse(
                any(e['op'] in ('acquire', 'key', 'clipboard_set') for e in self.events)
            )

    def test_agent_keys_use_independent_lease_without_focus_or_clipboard(self):
        self.agent_keyboard = True
        result = self.ca('key', '--window', 'target', 'Ctrl+Shift+S')
        self.assertEqual(result.returncode, 0, result.stderr)
        keys = [event for event in self.events if event['op'] == 'key']
        self.assertEqual([event['code'] for event in keys], [29, 42, 31, 31, 42, 29])
        self.assertEqual([event['pressed'] for event in keys], [True] * 3 + [False] * 3)
        self.assertTrue(all(event['lane'] == 'agent' for event in keys))
        self.assertFalse(any(event['op'] in ('focus', 'clipboard_set') for event in self.events))
        self.assertEqual(self.events[-1]['op'], 'release')

    def test_agent_keys_refuse_target_without_ready_keyboard(self):
        self.agent_keyboard = True
        self.agent_keyboard_ready = False
        result = self.ca('key', '--window', 'target', 'W')
        self.assertEqual(result.returncode, 1)
        self.assertIn('not ready', result.stderr)
        self.assertFalse(any(event['op'] in ('key', 'focus') for event in self.events))
        self.assertEqual(self.events[-1]['op'], 'release')

    def test_outside_coordinates_do_not_dispatch_input(self):
        result = self.ca('click', '--window', 'target', '--x', '300', '--y', '0')
        self.assertEqual(result.returncode, 1)
        self.assertFalse(any(e['op'] in ('move', 'button') for e in self.events))
        self.assertFalse(any(e['op'] == 'acquire' for e in self.events))

    def test_stop_uses_external_takeover(self):
        result = self.ca('stop')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.events[0]['op'], 'takeover')

    def test_socket_option_before_or_after_command_overrides_environment(self):
        self.env['CA_SOCKET'] = str(self.path / 'missing')
        for args in [
            ('--socket', str(self.path / 'control'), 'windows'),
            ('windows', '--socket', str(self.path / 'control')),
        ]:
            result = self.ca(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['windows'][0]['id'], 'target')

    def test_host_flag_is_explicit_and_session_open_is_removed(self):
        for args in [
            ('--host', 'move', '--window', 'target', '--x', '5', '--y', '8'),
            ('move', '--host', '--window', 'target', '--x', '5', '--y', '8'),
        ]:
            self.events.clear()
            result = self.ca(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(all(e['lane'] == 'host' for e in self.events))
        self.events.clear()
        self.assertEqual(self.ca('session', 'open').returncode, 2)
        self.assertEqual(self.events, [])
        self.assertEqual(self.ca('focus', '--window', 'target').returncode, 1)
        self.assertFalse(any(e['op'] == 'acquire' for e in self.events))

    def test_set_name_resolves_actions_and_renames_existing_layout(self):
        old = self.path / 'window' / 'layout' / 'target'
        old.mkdir(parents=True)
        (old / 'window.json').write_text('{"id":"target"}')
        result = self.ca('set', 'target', '--name', 'kolourpaint')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['window'], 'target')
        self.assertTrue((self.path / 'window' / 'layout' / 'kolourpaint' / 'window.json').exists())
        self.assertFalse(old.exists())
        listed = json.loads(self.ca('windows').stdout)['windows']
        self.assertEqual(listed[0]['name'], 'kolourpaint')
        self.events.clear()
        self.assertEqual(
            self.ca('click', '--window', 'kolourpaint', '--x', '20', '--y', '30').returncode, 0
        )
        self.assertEqual(next(e['window'] for e in self.events if e['op'] == 'acquire'), 'target')
        self.assertEqual(self.ca('set', 'target', '--name', 'paint').returncode, 0)
        self.assertTrue((self.path / 'window' / 'layout' / 'paint' / 'window.json').exists())
        self.assertFalse((self.path / 'window' / 'layout' / 'kolourpaint').exists())

    def test_live_name_collision_and_stale_name_rebinding(self):
        self.window_ids = ['target', 'other']
        self.assertEqual(self.ca('set', 'target', '--name', 'paint').returncode, 0)
        (self.path / 'window' / 'layout' / 'paint').mkdir(parents=True)
        (self.path / 'window' / 'layout' / 'paint' / 'window.json').write_text(
            json.dumps({'title': 'Untitled — KolourPaint'})
        )
        collision = self.ca('set', 'other', '--name', 'paint')
        self.assertEqual(collision.returncode, 1)
        self.assertIn('already belongs', collision.stderr)
        self.assertEqual(self.ca('set', 'other', '--name', '../unsafe').returncode, 1)
        self.assertEqual(
            self.ca('click', '--window', 'paint', '--x', '1', '--y', '1').returncode, 0
        )
        self.window_ids = ['other']
        self.events.clear()
        self.assertEqual(
            self.ca('click', '--window', 'paint', '--x', '1', '--y', '1').returncode, 1
        )
        self.assertFalse(any(e['op'] == 'acquire' for e in self.events))
        rebound = self.ca('set', '--name', 'paint', '--title', 'other')
        self.assertEqual(rebound.returncode, 0, rebound.stderr)
        binding = json.loads(rebound.stdout)
        self.assertEqual(binding['previous_window'], 'target')
        self.assertTrue(binding['layout_revalidation_required'])
        self.assertEqual(binding['previous_layout_title'], 'Untitled — KolourPaint')
        self.events.clear()
        self.assertEqual(
            self.ca('click', '--window', 'paint', '--x', '1', '--y', '1').returncode, 0
        )
        self.assertEqual(next(e['window'] for e in self.events if e['op'] == 'acquire'), 'other')

    def test_title_match_must_be_unique(self):
        self.window_ids = ['target', 'other']
        self.window_titles['other'] = 'Another KolourPaint'
        result = self.ca('set', '--name', 'paint', '--title', 'KolourPaint')
        self.assertEqual(result.returncode, 1)
        self.assertIn('matched 2 windows', result.stderr)
        self.assertFalse((self.path / 'window' / 'names.json').exists())
        self.assertEqual(self.ca('set', '--name', 'paint', '--title', 'missing').returncode, 1)
        self.assertEqual(
            self.ca('set', 'target', '--name', 'paint', '--title', 'Untitled').returncode, 1
        )
        chosen = self.ca('set', '--name', 'paint', '--title', 'Untitled')
        self.assertEqual(chosen.returncode, 0, chosen.stderr)
        self.assertEqual(json.loads(chosen.stdout)['window'], 'target')

    def test_host_old_plugin_is_rejected_before_input(self):
        self.old_plugin = True
        result = self.ca('--host', 'click', '--window', 'target', '--x', '2', '--y', '3')
        self.assertEqual(result.returncode, 1)
        self.assertIn('refusing fallback', result.stderr)
        self.assertEqual([e['op'] for e in self.events], ['capabilities'])

    def test_terminated_program_releases_lease(self):
        # Force termination during the acquire exchange, before its reply. This
        # previously left the reader/heartbeat sharing a desynchronized stream.
        self.acquire_reply_delay = 0.3
        process = subprocess.Popen(
            [str(ROOT / 'ca'), 'execute', '--window', 'target'],
            env=self.env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            process.stdin.write(
                'import time\ndef run(ctx):\n    ctx.move(x=1, y=1)\n    time.sleep(30)\n'
            )
            process.stdin.close()
            process.stdin = None
            self.assertTrue(self.acquired.wait(3))
            process.terminate()
            out, error = process.communicate(timeout=6)
            self.assertEqual(process.returncode, 1, error)
            self.assertEqual(json.loads(out)['interruption']['code'], 'user_cancelled')
            self.assertTrue(self.returned.wait(2))
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()


if __name__ == '__main__':
    unittest.main()
