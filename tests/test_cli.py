import json
import os
from pathlib import Path
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest

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
        self.acquire_reply_delay = 0
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
                    if op == 'windows':
                        reply['windows'] = [{'id': 'target', 'native': True, 'visible': True,
                                             'x': 100, 'y': 200, 'width': 300, 'height': 400}]
                    if op == 'capabilities':
                        reply['operations'] = ['focus','key']
                        if not test.old_plugin:
                            reply.update(protocol=3,lane=request.get('lane','agent'),host_pointer=True)
                    if op == 'focus' and test.reject_focus:
                        reply.update(ok=False, error='rejected')
                    try:
                        self.wfile.write((json.dumps(reply)+'\n').encode())
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

        self.server = socketserver.ThreadingUnixStreamServer(str(self.path/'control'), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.env = dict(os.environ, CA_SOCKET=str(self.path/'control'))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def ca(self, *args):
        return subprocess.run([str(ROOT/'ca'), *args], env=self.env, capture_output=True, text=True, timeout=8)

    def test_click_translates_window_coordinates_and_releases(self):
        result = self.ca('click', '--window', 'target', '--x', '20', '--y', '30')
        self.assertEqual(result.returncode, 0, result.stderr)
        motion = next(e for e in self.events if e['op']=='move')
        self.assertEqual((motion['x'], motion['y']), (120, 230))
        self.assertEqual(motion['lease'], 'test-lease')
        self.assertEqual(self.events[-1]['op'], 'release')
        self.assertEqual(json.loads(result.stdout)['status'], 'dispatched')

    def test_type_focuses_without_mouse_events(self):
        result = self.ca('type', '--window', 'target', 'Ab')
        self.assertEqual(result.returncode, 0, result.stderr)
        ops = [e['op'] for e in self.events]
        self.assertLess(ops.index('focus'), ops.index('key'))
        self.assertFalse(any(op in ('move', 'button') for op in ops))
        self.assertEqual(ops[-1], 'release')

    def test_unsupported_text_has_no_side_effects(self):
        result = self.ca('type', '--window', 'target', 'prefix 😀')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.events, [])

    def test_rejected_focus_returns_lease_without_typing(self):
        self.reject_focus = True
        result = self.ca('type', '--window', 'target', 'hello')
        self.assertEqual(result.returncode, 1)
        self.assertFalse(any(e['op']=='key' for e in self.events))
        self.assertEqual(self.events[-1]['op'], 'release')

    def test_outside_coordinates_do_not_dispatch_input(self):
        result = self.ca('click', '--window', 'target', '--x', '300', '--y', '0')
        self.assertEqual(result.returncode, 1)
        self.assertFalse(any(e['op'] in ('move', 'button') for e in self.events))
        self.assertEqual(self.events[-1]['op'], 'release')

    def test_stop_uses_external_takeover(self):
        result = self.ca('stop')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.events[0]['op'], 'takeover')

    def test_socket_option_before_or_after_command_overrides_environment(self):
        self.env['CA_SOCKET'] = str(self.path/'missing')
        for args in [('--socket', str(self.path/'control'), 'windows'),
                     ('windows', '--socket', str(self.path/'control'))]:
            result = self.ca(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['windows'][0]['id'], 'target')

    def test_host_flag_is_explicit_and_session_open_is_removed(self):
        for args in [('--host','move','--window','target','--x','5','--y','8'),
                     ('move','--host','--window','target','--x','5','--y','8')]:
            self.events.clear()
            result=self.ca(*args)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertTrue(all(e['lane']=='host' for e in self.events))
        self.events.clear()
        self.assertEqual(self.ca('session','open').returncode,2)
        self.assertEqual(self.events,[])
        self.assertEqual(self.ca('focus','--window','target').returncode,1)
        self.assertFalse(any(e['op']=='acquire' for e in self.events))

    def test_host_old_plugin_is_rejected_before_input(self):
        self.old_plugin=True
        result=self.ca('--host','click','--window','target','--x','2','--y','3')
        self.assertEqual(result.returncode,1)
        self.assertIn('refusing fallback',result.stderr)
        self.assertEqual([e['op'] for e in self.events],['capabilities'])

    def test_terminated_program_releases_lease(self):
        # Force termination during the acquire exchange, before its reply. This
        # previously left the reader/heartbeat sharing a desynchronized stream.
        self.acquire_reply_delay = .3
        program = self.path/'task.py'
        program.write_text('import time\ndef main(client):\n    with client.owned("target"):\n        time.sleep(30)\n')
        process = subprocess.Popen([str(ROOT/'ca'), 'run', str(program)], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertTrue(self.acquired.wait(3))
            process.terminate()
            out, error = process.communicate(timeout=6)
            self.assertEqual(process.returncode, 130, error)
            self.assertTrue(self.returned.wait(2))
        finally:
            if process.poll() is None:
                process.kill(); process.communicate()


if __name__ == '__main__':
    unittest.main()
