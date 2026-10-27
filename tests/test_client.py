"""Failure-path tests for the controller's ownership lifetime."""

import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from computer_artist.client import Client


class ControllerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'control'
        self.server = socket.socket(socket.AF_UNIX)
        self.server.bind(str(self.path))
        self.server.listen()
        self.events = []
        self.replies = {}
        self.delays = {}
        self.drips = {}
        self.released = threading.Event()
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        connection, _ = self.server.accept()
        lease = ''
        with connection, connection.makefile('rb') as stream:
            for line in stream:
                request = json.loads(line)
                self.events.append(request)
                if request['op'] == 'acquire':
                    lease = 'lease-1'
                if request['op'] == 'release':
                    lease = ''
                    self.released.set()
                reply = self.replies.get(
                    request['op'],
                    {'ok': True, 'lease': lease, 'generation': 7, 'keyboard_ready': True},
                )
                data = (json.dumps(reply) + '\n').encode()
                time.sleep(self.delays.get(request['op'], 0))
                try:
                    if request['op'] in self.drips:
                        for byte in data:
                            connection.sendall(bytes([byte]))
                            time.sleep(self.drips[request['op']])
                    else:
                        connection.sendall(data)
                except (BrokenPipeError, ConnectionResetError):
                    return

    def tearDown(self):
        self.server.close()
        self.thread.join(3)
        self.tmp.cleanup()

    def _host_windows(self, environment):
        self.replies['capabilities'] = {
            'ok': True,
            'lane': 'host',
            'host_pointer': True,
            'protocol': 3,
            'environment': environment,
        }
        self.replies['windows'] = {
            'ok': True,
            'generation': 1,
            'windows': [
                {
                    'id': 'kp',
                    'title': 'KolourPaint',
                    'native': True,
                    'visible': True,
                    'x': 640,
                    'y': 378,
                    'width': 640,
                    'height': 480,
                    'acquirable': False,
                    'agent': False,
                    'agent_keyboard': True,
                    'human_active': True,
                    'agent_restrictions': [
                        'human_pointer_in_application',
                        'human_keyboard_in_application',
                    ],
                    'host': False,
                    'host_acquirable': True,
                    'host_restrictions': [],
                }
            ],
        }
        with Client(self.path, lane='host', window_dir=Path(self.tmp.name) / 'window') as client:
            return client.windows()[0]

    def test_private_seat_reports_host_readiness_and_groups_agent_lane(self):
        window = self._host_windows('creation-bench')
        self.assertEqual((window['lane'], window['ready']), ('host', True))
        self.assertEqual(window['host_restrictions'], [])
        for field in ('acquirable', 'human_active', 'agent_restrictions'):
            self.assertNotIn(field, window)
        self.assertTrue(window['agent_lane']['human_active'])
        self.assertIn('human_pointer_in_application', window['agent_lane']['agent_restrictions'])
        self.assertEqual((window['x'], window['width']), (640, 640))

    def test_desktop_host_lane_keeps_agent_fields(self):
        window = self._host_windows('')
        self.assertNotIn('agent_lane', window)
        self.assertFalse(window['acquirable'])
        self.assertTrue(window['human_active'])

    def test_deadline_returns_app_while_program_is_idle(self):
        with Client(self.path, deadline=0.1) as client:
            client.acquire('existing-window')
            self.assertTrue(self.released.wait(2))
            with self.assertRaises(TimeoutError):
                client.move(1, 2)
        self.assertFalse(any(e['op'] == 'move' for e in self.events))

    def test_program_exception_releases_lease(self):
        with Client(self.path) as client:
            with self.assertRaisesRegex(ValueError, 'program failed'):
                with client.owned('existing-window'):
                    client.move(20, 30)
                    raise ValueError('program failed')
        self.assertTrue(self.released.is_set())
        action = next(e for e in self.events if e['op'] == 'move')
        self.assertEqual(action['lease'], 'lease-1')
        self.assertEqual(action['generation'], 7)

    def test_action_budget_releases_without_dispatching_extra_action(self):
        with Client(self.path, action_budget=1) as client:
            client.acquire('existing-window')
            with self.assertRaises(TimeoutError):
                client.move(20, 30)
            self.assertTrue(self.released.is_set())
        self.assertFalse(any(e['op'] == 'move' for e in self.events))

    def support_agent_keyboard(self):
        self.replies['capabilities'] = {
            'ok': True,
            'keyboard': True,
            'operations': ['key'],
        }

    def test_agent_keyboard_capability_gate_rejects_legacy_before_input(self):
        with Client(self.path) as client:
            with self.assertRaisesRegex(ValueError, 'does not support independent'):
                client.require_keyboard()
        self.assertFalse(any(e['op'] in ('acquire', 'key', 'focus') for e in self.events))

    def test_agent_chord_budget_failure_reconciles_held_keys(self):
        self.support_agent_keyboard()
        with Client(self.path, action_budget=4) as client:
            client.require_keyboard()
            client.acquire('existing-window')
            with self.assertRaises(TimeoutError):
                client.chord(29, 31)
            self.assertEqual(client.held_keys, set())
        self.assertTrue(any(event['op'] == 'cancel' for event in self.events))
        self.assertTrue(self.released.is_set())

    def test_invalid_reply_poisoned_transport_does_not_dispatch_again(self):
        self.replies['windows'] = []
        with Client(self.path) as client:
            with self.assertRaisesRegex(ConnectionError, 'invalid protocol reply'):
                client.windows()
            with self.assertRaises(ConnectionError):
                client.move(1, 2)
        self.assertEqual([e['op'] for e in self.events], ['windows'])

    def test_click_cleanup_preserves_release_error(self):
        with Client(self.path) as client:
            client.acquire('existing-window')
            original = client.button

            def button(code=272, pressed=True):
                if not pressed:
                    raise ValueError('release failed')
                return original(code, pressed)

            client.button = button
            self.replies['cancel'] = {'ok': False, 'error': 'already revoked'}
            with self.assertRaisesRegex(ValueError, 'release failed'):
                client.click(1, 2)
        self.assertTrue(any(e['op'] == 'cancel' for e in self.events))

    def test_click_waits_for_the_pen_to_glide_before_pressing(self):
        self.replies['move'] = {'ok': True, 'glide_ms': 120}
        with Client(self.path) as client:
            client.acquire('existing-window')
            started = time.monotonic()
            client.click(400, 300)
            self.assertGreaterEqual(time.monotonic() - started, 0.12)
        self.assertEqual([e['op'] for e in self.events][1:4], ['move', 'button', 'button'])

    def test_path_keyboard_interrupt_cancels_held_button(self):
        with Client(self.path) as client:
            client.acquire('existing-window')

            def points():
                yield (1, 2)
                raise KeyboardInterrupt('stop')

            with self.assertRaises(KeyboardInterrupt):
                client.path(points(), interval=0)
        self.assertTrue(any(e['op'] == 'button' and e['pressed'] for e in self.events))
        self.assertTrue(any(e['op'] == 'cancel' for e in self.events))

    def test_path_long_interval_respects_deadline(self):
        with Client(self.path, deadline=0.08) as client:
            client.acquire('existing-window')
            started = time.monotonic()
            with self.assertRaises(TimeoutError):
                client.path([(1, 2), (2, 3)], interval=30)
            self.assertLess(time.monotonic() - started, 0.5)
        self.assertTrue(self.released.is_set())

    def test_delayed_reply_obeys_short_deadline_and_close_preserves_error(self):
        self.delays['move'] = 0.6
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            with Client(self.path, deadline=0.08) as client:
                with client.owned('existing-window'):
                    client.move(1, 2)
        self.assertLess(time.monotonic() - started, 0.3)
        self.assertIsInstance(client.failure, TimeoutError)
        self.assertFalse(client.lease)

    def test_trickled_reply_does_not_restart_exchange_deadline(self):
        self.drips['windows'] = 0.04
        started = time.monotonic()
        with Client(self.path, deadline=0.12) as client:
            with self.assertRaises(TimeoutError):
                client.windows()
        self.assertLess(time.monotonic() - started, 0.3)

    def test_cleanup_timeout_preserves_program_error(self):
        self.delays['release'] = 0.8
        started = time.monotonic()
        with Client(self.path) as client:
            with self.assertRaisesRegex(ValueError, 'program failed'):
                with client.owned('existing-window'):
                    raise ValueError('program failed')
            self.assertFalse(client.lease)
        self.assertLess(time.monotonic() - started, 0.7)


if __name__ == '__main__':
    unittest.main()
