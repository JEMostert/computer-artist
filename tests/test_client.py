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

    def test_exhausted_budget_returns_app_while_program_is_idle(self):
        with Client(self.path, action_budget=1) as client:
            client.acquire('existing-window')
            self.assertTrue(self.released.wait(2))
            client.heartbeat.join(0.5)
            self.assertIsInstance(client.failure, TimeoutError)

    def test_agent_text_does_not_dispatch(self):
        with Client(self.path) as client:
            with client.owned('existing-window'):
                with self.assertRaises(ValueError):
                    client.type_text('valid prefix 😀')
        self.assertFalse(any(e['op'] == 'key' for e in self.events))
        self.assertFalse(any(e['op'] == 'clipboard_set' for e in self.events))

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

    def test_agent_chords_keep_host_focus_and_release_keys_in_reverse_order(self):
        self.support_agent_keyboard()
        with Client(self.path) as client:
            client.require_keyboard()
            client.acquire('existing-window')
            client.chord(29, 31)
            self.assertEqual(client.held_keys, set())
        keys = [event for event in self.events if event['op'] == 'key']
        self.assertEqual([event['code'] for event in keys], [29, 31, 31, 29])
        self.assertTrue(all(event['lane'] == 'agent' for event in keys))
        self.assertFalse(any(event['op'] in ('focus', 'clipboard_set') for event in self.events))

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

    def test_first_key_begins_independent_focus_once_per_lease(self):
        self.support_agent_keyboard()
        self.replies['capabilities']['operations'].append('keyboard_begin')
        with Client(self.path) as client:
            client.acquire('existing-window')
            client.chord(29, 31)
            client.chord(30)
            client.release()
            client.acquire('existing-window')
            client.chord(31)
        operations = [e['op'] for e in self.events]
        self.assertEqual(operations.count('keyboard_begin'), 2)
        first_begin = next(e for e in self.events if e['op'] == 'keyboard_begin')
        self.assertEqual(first_begin['lease'], 'lease-1')
        self.assertEqual(first_begin['lane'], 'agent')
        self.assertLess(operations.index('keyboard_begin'), operations.index('key'))
        self.assertNotIn('focus', operations)

    def test_first_keyboard_notification_still_respects_hard_budget(self):
        self.support_agent_keyboard()
        self.replies['capabilities']['operations'].append('keyboard_begin')
        with Client(self.path, action_budget=3) as client:
            client.acquire('existing-window')
            with self.assertRaises(TimeoutError):
                client.chord(31)
            self.assertEqual(client.held_keys, set())
        self.assertFalse(any(e['op'] == 'key' for e in self.events))

    def test_agent_key_requires_acquired_and_ready_target(self):
        self.support_agent_keyboard()
        self.replies['acquire'] = {
            'ok': True,
            'lease': 'lease-1',
            'generation': 7,
            'keyboard_ready': False,
        }
        self.replies['ping'] = {'ok': True, 'keyboard_ready': False}
        with Client(self.path) as client:
            with self.assertRaisesRegex(ValueError, 'acquired'):
                client.key(31, True)
            client.acquire('existing-window')
            with self.assertRaisesRegex(ValueError, 'not ready'):
                client.key(31, True)
        self.assertFalse(any(event['op'] in ('key', 'focus') for event in self.events))

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

    def test_invalid_path_interval_does_not_dispatch(self):
        with Client(self.path) as client:
            for interval in (-1, float('nan'), float('inf')):
                with self.assertRaises(ValueError):
                    client.path([(1, 2)], interval=interval)
        self.assertEqual(self.events, [])

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
